from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import re
from typing import Any

import requests


class DiscoveryError(RuntimeError):
    """Raised when an authorized public job feed cannot be read."""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)


def strip_html(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    return " ".join(parser.parts)


def _request_json(url: str) -> Any:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "LocalJobApplicationAssistant/1.0"},
            timeout=20,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise DiscoveryError(f"Unable to read a public job feed: {exc}") from exc


def _level(title: str) -> str:
    lower = title.lower()
    if any(term in lower for term in ("intern", "working student", "werkstudent")):
        return "Internship"
    if any(term in lower for term in ("senior", "lead", "principal", "head of")):
        return "Senior"
    if any(term in lower for term in ("junior", "entry", "graduate", "associate")):
        return "Entry level"
    return "Unknown"


def _workplace(text: str) -> str:
    lower = text.lower()
    if "remote" in lower:
        return "Remote"
    if "hybrid" in lower:
        return "Hybrid"
    if "on-site" in lower or "onsite" in lower or "on site" in lower:
        return "Onsite"
    return "Unknown"


def _employment(text: str) -> str:
    lower = text.lower()
    if "full-time" in lower or "full time" in lower or "fulltime" in lower:
        return "Full-time"
    if "part-time" in lower or "part time" in lower:
        return "Part-time"
    return "Unknown"


def _date(value: Any) -> str:
    if not value:
        return ""
    try:
        if isinstance(value, (int, float)):
            parsed = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.date().isoformat()
    except (ValueError, OverflowError, OSError):
        return ""


def fetch_greenhouse(board: str) -> list[dict[str, Any]]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", board):
        raise ValueError("Greenhouse board names may contain only letters, digits, _ and -.")
    payload = _request_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
    jobs = []
    for item in payload.get("jobs", []):
        content = item.get("content") or ""
        offices = item.get("offices") or []
        location = ", ".join(
            office.get("name", "") for office in offices if office.get("name")
        ) or (item.get("location") or {}).get("name", "")
        title = item.get("title", "")
        description = strip_html(content)
        text = f"{title} {description}"
        jobs.append(
            {
                "source": "Greenhouse",
                "external_id": str(item["id"]),
                "company": board,
                "title": title,
                "location": location,
                "url": item.get("absolute_url", ""),
                "description": description,
                "posted_at": "",
                "experience_level": _level(title),
                "workplace_type": _workplace(f"{location} {text}"),
                "employment_type": _employment(text),
            }
        )
    return [job for job in jobs if job["url"] and job["title"]]


def fetch_lever(site: str) -> list[dict[str, Any]]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", site):
        raise ValueError("Lever site names may contain only letters, digits, _ and -.")
    payload = _request_json(f"https://api.lever.co/v0/postings/{site}?mode=json")
    jobs = []
    for item in payload:
        categories = item.get("categories") or {}
        commitment = categories.get("commitment", "")
        title = item.get("text", "")
        description = strip_html(item.get("description", ""))
        workplace = item.get("workplaceType", "")
        jobs.append(
            {
                "source": "Lever",
                "external_id": str(item.get("id", "")),
                "company": site,
                "title": title,
                "location": categories.get("location", ""),
                "url": item.get("hostedUrl", ""),
                "description": " ".join(
                    part for part in (description, strip_html(item.get("additional", ""))) if part
                ),
                "posted_at": _date(item.get("createdAt")),
                "experience_level": categories.get("level", "") or _level(title),
                "workplace_type": workplace.title() if workplace else _workplace(title),
                "employment_type": commitment or _employment(title),
            }
        )
    return [job for job in jobs if job["external_id"] and job["url"] and job["title"]]


def discover(
    greenhouse_boards: list[str],
    lever_sites: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    found: list[dict[str, Any]] = []
    errors: list[str] = []
    for source, names, fetcher in (
        ("Greenhouse", greenhouse_boards, fetch_greenhouse),
        ("Lever", lever_sites, fetch_lever),
    ):
        for name in names:
            try:
                found.extend(fetcher(name.strip()))
            except (DiscoveryError, ValueError) as exc:
                errors.append(f"{source} board '{name.strip()}': {exc}")
    return found, errors


def matches_filters(job: dict[str, Any], filters: dict[str, Any]) -> bool:
    title_terms = [term.lower() for term in filters.get("titles", []) if term.strip()]
    if title_terms and not any(term in job["title"].lower() for term in title_terms):
        return False
    countries = [term.lower() for term in filters.get("countries", []) if term.strip()]
    if countries and not any(term in job["location"].lower() for term in countries):
        return False
    cities = [term.lower() for term in filters.get("cities", []) if term.strip()]
    if cities and not any(term in job["location"].lower() for term in cities):
        return False
    locations = [term.lower() for term in filters.get("locations", []) if term.strip()]
    if locations and not any(term in job["location"].lower() for term in locations):
        return False
    experience = filters.get("experience_level", "Any")
    if experience != "Any":
        level = job["experience_level"].lower()
        title = job["title"].lower()
        if experience == "Entry-level / Junior / Associate":
            is_entry = any(term in level for term in ("entry", "junior", "associate", "graduate"))
            is_entry = is_entry or any(
                term in title for term in ("junior", "associate", "graduate", "entry level")
            )
            if not is_entry:
                return False
        elif level != experience.lower():
            return False
    workplace = filters.get("workplace_type", "Any")
    if workplace != "Any" and job["workplace_type"].lower() != workplace.lower():
        return False
    if filters.get("full_time_only") and job["employment_type"].lower() != "full-time":
        return False
    days = filters.get("posted_within_days")
    if days:
        if not job["posted_at"]:
            return False
        try:
            posted = datetime.fromisoformat(job["posted_at"]).date()
        except ValueError:
            return False
        age = (datetime.now(timezone.utc).date() - posted).days
        if age < 0 or age > int(days):
            return False
    return True
