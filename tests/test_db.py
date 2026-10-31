import pytest

from job_agent.db import (
    create_application,
    get_setting,
    init_db,
    list_applications,
    list_jobs,
    save_job,
    save_match,
    set_setting,
    update_application,
)


def _job() -> dict:
    return {
        "source": "Lever",
        "external_id": "job-1",
        "company": "Example",
        "title": "Data Analyst",
        "location": "Berlin",
        "url": "https://jobs.example.test/1",
        "description": "Python and SQL",
        "posted_at": "2026-10-30",
        "experience_level": "Entry level",
        "workplace_type": "Hybrid",
        "employment_type": "Full-time",
    }


def test_job_upsert_match_and_application_tracking(tmp_path):
    database = tmp_path / "test.sqlite3"
    init_db(database)
    job_id = save_job(_job(), database)
    assert save_job({**_job(), "title": "Updated title"}, database) == job_id
    assert len(list_jobs(database_path=database)) == 1
    assert list_jobs(database_path=database)[0]["title"] == "Updated title"

    save_match(job_id, {"score": 82, "priority": "High"}, database)
    application_id = create_application(job_id, "resume.docx", "letter.docx", database)
    with pytest.raises(ValueError, match="cannot change from Pending approval to Submitted"):
        update_application(application_id, database, status="Submitted")
    update_application(
        application_id,
        database,
        status="Approved",
        resume_version="resume-v1.docx",
    )
    update_application(application_id, database, status="Submitted")
    application = list_applications(database)[0]
    assert application["status"] == "Submitted"
    assert application["match_score"] == 82
    assert application["resume_version"] == "resume-v1.docx"


def test_settings_round_trip_and_match_score_validation(tmp_path):
    database = tmp_path / "test.sqlite3"
    init_db(database)
    set_setting("locations", ["Germany", "Dubai"], database)
    assert get_setting("locations", database_path=database) == ["Germany", "Dubai"]
    try:
        save_match(999, {"score": 101}, database)
    except ValueError as exc:
        assert "between 0 and 100" in str(exc)
    else:
        raise AssertionError("Invalid match score was accepted.")


def test_linkedin_jobs_deduplicate_by_url_and_missing_description_clears_match(tmp_path):
    database = tmp_path / "test.sqlite3"
    init_db(database)
    job = {
        **_job(),
        "source": "LinkedIn",
        "external_id": "123456",
        "url": "https://www.linkedin.com/jobs/view/123456",
        "description": "Python and SQL",
    }
    job_id = save_job(job, database)
    save_match(job_id, {"score": 75, "priority": "Medium"}, database)
    updated = {**job, "company": "Updated Company", "description": ""}
    assert save_job(updated, database) == job_id
    saved = list_jobs(database_path=database)[0]
    assert saved["company"] == "Updated Company"
    assert saved["match_score"] is None
    assert saved["match_status"] == "Pending"
    assert len(list_jobs(database_path=database)) == 1
