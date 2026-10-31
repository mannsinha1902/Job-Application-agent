from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def _normal(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _field_label(field) -> str:
    return field.evaluate(
        """element => {
            const labels = element.labels ? Array.from(element.labels).map(label => label.innerText) : [];
            const parent = element.closest('label');
            if (parent) labels.push(parent.innerText);
            return labels.join(' ');
        }"""
    )


def _fill_fields(page, manifest: dict) -> int:
    profile = manifest["profile"]
    values = {
        "email": profile.get("email", ""),
        "phone": profile.get("phone", ""),
        "city": profile.get("city", ""),
        "linkedin": profile.get("linkedin", ""),
        "portfolio": profile.get("portfolio", ""),
    }
    full_name = profile.get("full_name", "")
    if full_name:
        values["full name"] = full_name
        parts = full_name.split()
        values["first name"] = parts[0]
        if len(parts) > 1:
            values["last name"] = parts[-1]

    count = 0
    fields = page.locator("input, textarea, select")
    for index in range(fields.count()):
        field = fields.nth(index)
        tag_name = field.evaluate("(element) => element.tagName.toLowerCase()")
        field_type = (field.get_attribute("type") or "text").lower()
        if field_type in {"password", "submit", "button", "hidden", "checkbox", "radio"}:
            continue
        associated_label = _field_label(field)
        if field_type == "file":
            label = _normal(
                " ".join(
                    filter(
                        None,
                        [
                            field.get_attribute("name"),
                            field.get_attribute("aria-label"),
                            field.get_attribute("accept"),
                            field.get_attribute("id"),
                        ],
                    )
                )
                + " "
                + associated_label
            )
            resume_path = manifest.get("resume_path")
            cover_path = manifest.get("cover_letter_path")
            if resume_path and "resume" in label:
                field.set_input_files(resume_path)
                count += 1
            elif cover_path and ("cover" in label or "letter" in label):
                field.set_input_files(cover_path)
                count += 1
            continue
        label = " ".join(
            filter(
                None,
                [
                    field.get_attribute("name"),
                    field.get_attribute("aria-label"),
                    field.get_attribute("placeholder"),
                    field.get_attribute("autocomplete"),
                    field.get_attribute("id"),
                    field.get_attribute("type"),
                    associated_label,
                ],
            )
        )
        normalized = _normal(label)
        # Avoid answering screening questions or filling arbitrary fields.
        if any(word in normalized for word in ("question", "why", "salary", "experience", "visa", "sponsor")):
            continue
        if tag_name == "select":
            continue
        match = next(
            (
                value
                for key, value in values.items()
                if value
                and (
                    (key == "email" and ("email" in normalized or field_type == "email"))
                    or (key == "phone" and ("phone" in normalized or field_type == "tel"))
                    or (key == "city" and any(word in normalized for word in ("city", "location")))
                    or (key == "linkedin" and "linkedin" in normalized)
                    or (key == "portfolio" and any(word in normalized for word in ("portfolio", "website")))
                    or (key == "full name" and any(term in normalized for term in ("full name", "your name", "candidate name")))
                    or (key == "first name" and "first name" in normalized)
                    or (key == "last name" and "last name" in normalized)
                    or (key == "full name" and normalized == "name")
                )
            ),
            "",
        )
        if match:
            field.fill(match)
            count += 1

    unanswered = []
    for index in range(fields.count()):
        field = fields.nth(index)
        if not field.is_visible() or not field.is_enabled():
            continue
        label = " ".join(
            filter(
                None,
                [
                    field.get_attribute("name"),
                    field.get_attribute("aria-label"),
                    field.get_attribute("placeholder"),
                    _field_label(field),
                ],
            )
        ).strip()
        normalized = _normal(label)
        is_screening = any(
            phrase in normalized
            for phrase in (
                "why",
                "authorization",
                "sponsorship",
                "work permit",
                "salary",
                "relocation",
                "notice period",
                "years of experience",
                "eligible to work",
            )
        )
        value = field.evaluate("(element) => element.value")
        if not value and (field.get_attribute("required") is not None or is_screening):
            unanswered.append(label or "Unlabeled required field")
    if unanswered:
        page.evaluate(
            """items => {
                const panel = document.createElement('aside');
                panel.setAttribute('role', 'status');
                panel.style.cssText = 'position:fixed;right:12px;top:12px;z-index:2147483647;'
                    + 'max-width:380px;max-height:45vh;overflow:auto;background:#fff7d6;'
                    + 'color:#222;padding:16px;border:2px solid #8a5a00;border-radius:8px;'
                    + 'font:14px/1.45 sans-serif;box-shadow:0 2px 12px #0005';
                const heading = document.createElement('strong');
                heading.textContent = 'Review unanswered required or screening fields';
                const list = document.createElement('ul');
                for (const item of items) {
                    const row = document.createElement('li');
                    row.textContent = item;
                    list.appendChild(row);
                }
                panel.append(heading, list);
                document.body.appendChild(panel);
            }""",
            unanswered,
        )
        print(f"Review unanswered fields in the browser: {unanswered}")
    return count


def main(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            page = browser.new_page()
            page.goto(manifest["url"], wait_until="domcontentloaded", timeout=60000)
            count = _fill_fields(page, manifest)
            print(
                f"Prepared {count} recognized field(s). Review the page and screening questions. "
                "This tool will not click Submit or Apply. Close the browser when finished."
            )
            page.wait_for_timeout(24 * 60 * 60 * 1000)
            browser.close()
    except Exception as exc:
        print(f"Browser preparation failed: {exc}", file=sys.stderr)
        raise
    finally:
        manifest_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
