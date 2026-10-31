import pytest

from job_agent.ai import AIServiceError, match_job


def test_ai_match_requires_explicit_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(AIServiceError, match="OPENAI_API_KEY"):
        match_job("Data Analyst", "Example", "SQL role", "Resume with SQL")
