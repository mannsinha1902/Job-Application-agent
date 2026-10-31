import pytest

from job_agent.ai import AIServiceError, has_openai_api_key, match_job


def test_ai_match_requires_explicit_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert has_openai_api_key() is False
    with pytest.raises(AIServiceError, match="OPENAI_API_KEY"):
        match_job("Data Analyst", "Example", "SQL role", "Resume with SQL")


def test_ai_match_rejects_missing_job_description_without_api_call():
    with pytest.raises(AIServiceError, match="description is required"):
        match_job("Data Analyst", "Example", "", "Resume with SQL")
