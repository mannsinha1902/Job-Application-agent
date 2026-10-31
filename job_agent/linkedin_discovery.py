from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.message import Message
from email.policy import default
from html.parser import HTMLParser
import imaplib
import re
import socket
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .discovery import _employment, _level, _workplace


class LinkedInImportError(RuntimeError):
    """Raised when an authorized LinkedIn alert import fails."""


def _canonical_job_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in {
        "linkedin.com",
        "www.linkedin.com",
    }:
        raise ValueError("Use a direct linkedin.com job posting URL.")
    if not re.search(r"/(?:comm/)?jobs/view/\d+", parsed.path):
        raise ValueError("The LinkedIn URL must point to a /jobs/view/<job-id> posting.")
    job_id = re.search(r"/(?:comm/)?jobs/view/(\d+)", parsed.path)
    if job_id is None:
        raise ValueError("The LinkedIn job URL does not contain a numeric job ID.")
    return urlunsplit(("https", "www.linkedin.com", f"/jobs/view/{job_id.group(1)}", "", ""))


def _date_from_relative(text: str) -> str:
    clean = text.strip().lower()
    if clean in {"today", "just now"}:
        return datetime.now(timezone.utc).date().isoformat()
    match = re.search(r"(\d+)\s+(hour|day|week|month)s?\s+ago", clean)
    if not match:
        return ""
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "hour":
        elapsed = timedelta(hours=amount)
    elif unit == "day":
        elapsed = timedelta(days=amount)
    elif unit == "week":
        elapsed = timedelta(weeks=amount)
    else:
        elapsed = timedelta(days=amount * 30)
    return (datetime.now(timezone.utc) - elapsed).date().isoformat()


class _AlertHTMLParser(HTMLParser):
    _BLOCK_TAGS = {
        "br",
        "div",
        "li",
        "p",
        "td",
        "tr",
        "table",
        "h1",
        "h2",
        "h3",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.text_length = 0
        self.anchors: list[dict[str, Any]] = []
        self._active_anchor: dict[str, Any] | None = None

    def _append(self, value: str) -> None:
        self.text_parts.append(value)
        self.text_length += len(value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in self._BLOCK_TAGS:
            self._append("\n")
        if tag == "a" and attributes.get("href"):
            self._active_anchor = {
                "url": attributes["href"],
                "start": self.text_length,
                "title_parts": [],
            }

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._active_anchor is not None:
            anchor = self._active_anchor
            anchor["end"] = self.text_length
            anchor["title"] = " ".join("".join(anchor.pop("title_parts")).split())
            self.anchors.append(anchor)
            self._active_anchor = None
        if tag in self._BLOCK_TAGS:
            self._append("\n")

    def handle_data(self, data: str) -> None:
        self._append(data)
        if self._active_anchor is not None:
            self._active_anchor["title_parts"].append(data)


def _message_bodies(message: Message) -> tuple[str, str]:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_maintype() == "multipart":
            continue
        content_type = part.get_content_type()
        try:
            content = part.get_content()
        except (LookupError, UnicodeDecodeError):
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if not isinstance(content, str):
            continue
        if content_type == "text/html":
            html_parts.append(content)
        elif content_type == "text/plain":
            plain_parts.append(content)
    return "\n".join(plain_parts), "\n".join(html_parts)


def _context_for_anchor(
    anchor: dict[str, Any],
    anchors: list[dict[str, Any]],
    full_text: str,
) -> list[str]:
    next_start = min(
        (item["start"] for item in anchors if item["start"] > anchor["end"]),
        default=min(len(full_text), anchor["end"] + 500),
    )
    context = full_text[anchor["end"] : min(next_start, anchor["end"] + 500)]
    return [
        re.sub(r"\s+", " ", line).strip(" \t|·-")
        for line in context.splitlines()
        if re.sub(r"\s+", " ", line).strip(" \t|·-")
    ]


def _jobs_from_alert_html(html: str) -> list[dict[str, Any]]:
    parser = _AlertHTMLParser()
    parser.feed(html)
    full_text = "".join(parser.text_parts)
    jobs = []
    for anchor in parser.anchors:
        try:
            url = _canonical_job_url(anchor["url"])
        except ValueError:
            continue
        title = anchor["title"].strip()
        if not title or len(title) > 180:
            continue
        lines = _context_for_anchor(anchor, parser.anchors, full_text)
        company = ""
        location = ""
        posted_at = ""
        for line in lines:
            if line.lower() in {"view job", "view job alert", "apply", "promoted"}:
                continue
            parts = [part.strip() for part in re.split(r"\s+[·|]\s+", line) if part.strip()]
            if parts:
                if not company:
                    company = parts[0]
                if len(parts) > 1 and not location:
                    location = parts[1]
                posted_at = next(
                    (parsed for parsed in (_date_from_relative(part) for part in parts) if parsed),
                    posted_at,
                )
                if company and location:
                    break
        jobs.append(
            {
                "source": "LinkedIn",
                "external_id": url.rsplit("/", 1)[-1],
                "company": company or "Unknown",
                "title": title,
                "location": location,
                "url": url,
                "description": "",
                "posted_at": posted_at,
                "experience_level": _level(title),
                "workplace_type": _workplace(f"{title} {location}"),
                "employment_type": _employment(title),
            }
        )
    return jobs


def parse_linkedin_alert_email(raw_message: bytes) -> list[dict[str, Any]]:
    """Extract direct LinkedIn job links from an exported alert email (.eml)."""
    try:
        message = message_from_bytes(raw_message, policy=default)
    except (ValueError, TypeError) as exc:
        raise ValueError("The uploaded file is not a valid email message.") from exc
    plain, html = _message_bodies(message)
    if html:
        jobs = _jobs_from_alert_html(html)
        if jobs:
            return jobs
    if plain:
        parser = _AlertHTMLParser()
        for match in re.finditer(
            r"https?://(?:www\.)?linkedin\.com/(?:comm/)?jobs/view/\d+[^\s<>]*",
            plain,
        ):
            try:
                url = _canonical_job_url(match.group(0).rstrip(").,;]"))
            except ValueError:
                continue
            prefix = plain[: match.start()]
            title_match = re.search(r"([^\n\r]{3,180})\s*$", prefix)
            title = title_match.group(1).strip(" \t-") if title_match else ""
            if title:
                parser.anchors.append({"url": url, "title": title, "start": match.start(), "end": match.end()})
        return [
            {
                "source": "LinkedIn",
                "external_id": item["url"].rsplit("/", 1)[-1],
                "company": "Unknown",
                "title": item["title"],
                "location": "",
                "url": item["url"],
                "description": "",
                "posted_at": "",
                "experience_level": _level(item["title"]),
                "workplace_type": _workplace(item["title"]),
                "employment_type": _employment(item["title"]),
            }
            for item in parser.anchors
        ]
    return []


def manual_linkedin_job(
    url: str,
    title: str,
    company: str,
    location: str = "",
    description: str = "",
    posted_at: str = "",
    experience_level: str = "",
    workplace_type: str = "",
    employment_type: str = "",
) -> dict[str, Any]:
    canonical_url = _canonical_job_url(url)
    if not title.strip() or not company.strip():
        raise ValueError("Job title and company are required.")
    if len(description) > 50000:
        raise ValueError("Job descriptions must be 50,000 characters or fewer.")
    if posted_at:
        datetime.fromisoformat(posted_at)
    return {
        "source": "LinkedIn",
        "external_id": canonical_url.rsplit("/", 1)[-1],
        "company": company.strip(),
        "title": title.strip(),
        "location": location.strip(),
        "url": canonical_url,
        "description": description.strip(),
        "posted_at": posted_at,
        "experience_level": experience_level or _level(title),
        "workplace_type": workplace_type or _workplace(f"{location} {description}"),
        "employment_type": employment_type or _employment(f"{title} {description}"),
    }


def fetch_gmail_linkedin_alerts(
    username: str,
    app_password: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Read recent LinkedIn alerts from Gmail over IMAP without marking them read."""
    if not username.strip() or not app_password:
        raise ValueError("Enter your Gmail address and an authorized Gmail app password.")
    if not 1 <= limit <= 100:
        raise ValueError("Email import limit must be between 1 and 100.")
    mailbox: imaplib.IMAP4_SSL | None = None
    try:
        mailbox = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=20)
        status, _ = mailbox.login(username.strip(), app_password)
        if status != "OK":
            raise LinkedInImportError("Gmail IMAP authorization failed; check the address and app password.")
        status, _ = mailbox.select("INBOX", readonly=True)
        if status != "OK":
            raise LinkedInImportError("Could not open the Gmail inbox in read-only mode.")
        status, data = mailbox.search(None, "FROM", "linkedin")
        if status != "OK" or not data or not data[0]:
            return []
        message_ids = data[0].split()[-limit:]
        jobs: list[dict[str, Any]] = []
        for message_id in reversed(message_ids):
            status, fetched = mailbox.fetch(message_id, "(BODY.PEEK[])")
            if status != "OK":
                continue
            raw_email = next(
                (part[1] for part in fetched if isinstance(part, tuple) and isinstance(part[1], bytes)),
                None,
            )
            if raw_email:
                jobs.extend(parse_linkedin_alert_email(raw_email))
        return jobs
    except LinkedInImportError:
        raise
    except (imaplib.IMAP4.error, OSError, socket.timeout) as exc:
        raise LinkedInImportError(
            "Could not read LinkedIn alert emails. Check IMAP access, Gmail app-password authorization, and network availability."
        ) from exc
    finally:
        if mailbox is not None:
            try:
                mailbox.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
