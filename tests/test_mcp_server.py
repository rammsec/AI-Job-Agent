import json

from src.mcp_server import add_job, get_job_by_url, search_jobs


def test_mcp_add_job_and_lookup_round_trip(tmp_path):
    db_path = tmp_path / "career_agent.db"
    job = {
        "source": "Adzuna Australia",
        "search_key": "python-sydney",
        "external_id": "job-mcp-1",
        "title": "Power BI Developer",
        "description": "Build Power BI reports and dashboards",
        "url": "https://example.com/jobs/job-mcp-1",
        "company": "Agent Labs",
        "location": "Sydney NSW",
        "posted_at": "2026-09-30T12:00:00Z",
        "salary_min": 140000,
    }

    result = add_job(job, db_path=db_path)
    assert result["saved"] is True
    assert result["job"]["external_id"] == "job-mcp-1"

    found = search_jobs("power bi", db_path=db_path, limit=10)
    assert found["total"] == 1
    assert found["jobs"][0]["title"] == "Power BI Developer"

    record = get_job_by_url("https://example.com/jobs/job-mcp-1", db_path=db_path)
    assert record["found"] is True
    assert record["job"]["company"] == "Agent Labs"


def test_mcp_rejects_jobs_outside_the_requested_scope(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="Only Australian"):
        add_job({
            "title": "Product Designer",
            "company": "Example Co",
            "location": "Melbourne VIC",
            "url": "https://example.com/jobs/designer",
        }, db_path=tmp_path / "career_agent.db")


def test_mcp_search_handles_missing_jobs_gracefully(tmp_path):
    db_path = tmp_path / "career_agent.db"
    result = search_jobs("nonexistent", db_path=db_path, limit=10)
    assert result["total"] == 0
    assert result["jobs"] == []
