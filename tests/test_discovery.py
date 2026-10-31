from job_agent.discovery import fetch_lever, matches_filters, strip_html


def test_strip_html():
    assert strip_html("<p>Python &amp; SQL</p><p>Data work</p>") == "Python & SQL Data work"


def test_lever_feed_normalization(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return [
                {
                    "id": "abc",
                    "text": "Junior Data Analyst",
                    "hostedUrl": "https://jobs.example.test/abc",
                    "createdAt": 1793318400000,
                    "description": "<p>Use SQL and Python</p>",
                    "workplaceType": "hybrid",
                    "categories": {
                        "location": "Berlin, Germany",
                        "commitment": "Full-time",
                        "level": "Entry level",
                    },
                }
            ]

    def fake_get(url, headers, timeout):
        assert url.endswith("/example?mode=json")
        assert headers["User-Agent"].startswith("LocalJob")
        assert timeout == 20
        return Response()

    monkeypatch.setattr("job_agent.discovery.requests.get", fake_get)
    job = fetch_lever("example")[0]
    assert job["company"] == "example"
    assert job["employment_type"] == "Full-time"
    assert job["workplace_type"] == "Hybrid"
    assert "Python" in job["description"]
    assert matches_filters(
        job,
        {
            "titles": ["Data Analyst"],
            "locations": ["Germany"],
            "experience_level": "Entry level",
            "workplace_type": "Hybrid",
            "full_time_only": True,
            "posted_within_days": None,
        },
    )


def test_board_names_are_validated_before_request():
    try:
        fetch_lever("bad/name")
    except ValueError as exc:
        assert "site names" in str(exc)
    else:
        raise AssertionError("Invalid board name was accepted.")
