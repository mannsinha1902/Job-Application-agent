from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest

from job_agent.linkedin_discovery import (
    fetch_gmail_linkedin_alerts,
    manual_linkedin_job,
    parse_linkedin_alert_email,
)


def _alert_email() -> bytes:
    message = EmailMessage()
    message["From"] = "jobs-noreply@linkedin.com"
    message["Subject"] = "Jobs matching your preferences"
    message.set_content(
        """<html><body>
        <h2><a href="https://www.linkedin.com/jobs/view/123456/?trackingId=abc">Junior Data Analyst</a></h2>
        <p>Example GmbH · Berlin, Germany · 2 days ago</p>
        </body></html>""",
        subtype="html",
    )
    return message.as_bytes()


def test_parse_linkedin_alert_email_extracts_job_details():
    jobs = parse_linkedin_alert_email(_alert_email())
    assert len(jobs) == 1
    job = jobs[0]
    assert job["source"] == "LinkedIn"
    assert job["external_id"] == "123456"
    assert job["title"] == "Junior Data Analyst"
    assert job["company"] == "Example GmbH"
    assert job["location"] == "Berlin, Germany"
    assert job["url"] == "https://www.linkedin.com/jobs/view/123456"
    assert job["description"] == ""
    expected_date = (datetime.now(timezone.utc).date() - timedelta(days=2)).isoformat()
    assert job["posted_at"] == expected_date


def test_manual_linkedin_import_requires_direct_job_url_and_fields():
    job = manual_linkedin_job(
        "https://www.linkedin.com/jobs/view/654321?trackingId=xyz",
        "IAM Analyst",
        "Example AG",
        "Munich, Germany",
        "Full-time role using SailPoint and Okta.",
    )
    assert job["url"] == "https://www.linkedin.com/jobs/view/654321"
    assert job["employment_type"] == "Full-time"
    assert job["source"] == "LinkedIn"
    legacy_url = manual_linkedin_job(
        "https://www.linkedin.com/comm/jobs/view/654322",
        "Analyst",
        "Example AG",
    )
    assert legacy_url["url"] == "https://www.linkedin.com/jobs/view/654322"
    with pytest.raises(ValueError, match="direct linkedin.com"):
        manual_linkedin_job("https://example.com/jobs/123", "Analyst", "Example")
    with pytest.raises(ValueError, match="required"):
        manual_linkedin_job("https://linkedin.com/jobs/view/123", "", "Example")


def test_gmail_import_uses_read_only_search_and_peek(monkeypatch):
    raw_email = _alert_email()

    class FakeMailbox:
        def __init__(self, host, port, timeout):
            assert host == "imap.gmail.com"
            assert port == 993
            assert timeout == 20
            self.fetch_calls = []
            self.readonly = False
            self.logged_out = False

        def login(self, username, password):
            assert username == "candidate@example.com"
            assert password == "one-time-password"
            return "OK", []

        def select(self, mailbox, readonly=False):
            assert mailbox == "INBOX"
            self.readonly = readonly
            return "OK", []

        def search(self, charset, *criteria):
            assert charset is None
            assert criteria == ("FROM", "linkedin")
            return "OK", [b"1"]

        def fetch(self, message_id, query):
            self.fetch_calls.append((message_id, query))
            return "OK", [(b"1 (BODY[])", raw_email)]

        def logout(self):
            self.logged_out = True
            return "OK", []

    fake = FakeMailbox("imap.gmail.com", 993, 20)
    def fake_imap(host, port, timeout):
        assert host == "imap.gmail.com"
        assert port == 993
        assert timeout == 20
        return fake

    monkeypatch.setattr("job_agent.linkedin_discovery.imaplib.IMAP4_SSL", fake_imap)
    jobs = fetch_gmail_linkedin_alerts(
        "candidate@example.com",
        "one-time-password",
        limit=10,
    )
    assert jobs[0]["external_id"] == "123456"
    assert fake.readonly is True
    assert fake.fetch_calls == [(b"1", "(BODY.PEEK[])")]
    assert fake.logged_out is True
