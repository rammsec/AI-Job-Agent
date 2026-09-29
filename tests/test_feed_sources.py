from urllib.parse import parse_qs, urlparse

import pytest

from src.job_scanner import parse_feed_entries, scan_jobs_for_profile


RSS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Sample Jobs</title>
    <item>
      <title>Senior Python Developer</title>
      <link>https://example.com/jobs/1</link>
      <description>FastAPI, Azure, SQL, Python, REST APIs</description>
    </item>
    <item>
      <title>Data Analyst</title>
      <link>https://example.com/jobs/2</link>
      <description>Excel, reporting, Power BI</description>
    </item>
  </channel>
</rss>
"""

JSON_FEED = """{
  "items": [
    {"title": "Accounts Assistant", "link": "https://example.com/jobs/3", "description": "AP/AR, accounting, Xero, Excel"},
    {"title": "Finance Coordinator", "link": "https://example.com/jobs/4", "description": "Payroll, reconciliations, spreadsheets"}
  ]
}
"""

ADZUNA_RESPONSE = """{
  "results": [
    {
      "title": "Accounts Assistant",
      "description": "Process invoices and reconcile accounts",
      "redirect_url": "https://www.adzuna.com.au/details/123",
      "company": {"display_name": "Example Pty Ltd"},
      "location": {"display_name": "Melbourne VIC"}
    }
  ]
}
"""


def test_parse_feed_entries_handles_rss_and_json():
    rss_jobs = parse_feed_entries(RSS_FEED, "rss")
    json_jobs = parse_feed_entries(JSON_FEED, "json")

    assert rss_jobs[0]["title"] == "Senior Python Developer"
    assert json_jobs[0]["title"] == "Accounts Assistant"
    assert "python" in rss_jobs[0]["description"].lower()


def test_scan_jobs_for_profile_accepts_feed_sources():
    profile = {"keywords": ["python", "fastapi", "azure", "sql"], "years_experience": 5}
    sources = [{"name": "demo-feed", "source_type": "rss", "feed_url": "https://example.com/rss"}]

    jobs = scan_jobs_for_profile(
        profile,
        sources,
        fetcher=lambda url: RSS_FEED,
    )

    assert jobs[0]["title"] == "Senior Python Developer"
    assert jobs[0]["score"] > 0
    assert "python" in jobs[0]["matches"]


def test_scan_jobs_for_profile_fetches_and_normalizes_adzuna(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "test-app-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test-app-key")
    profile = {"keywords": ["accounts", "reconcile"], "years_experience": 3}
    sources = [{
        "name": "Adzuna Australia",
        "source_type": "adzuna",
        "what": "accounting assistant",
        "where": "Melbourne VIC",
    }]
    requested_urls = []

    jobs = scan_jobs_for_profile(
        profile,
        sources,
        fetcher=lambda url: requested_urls.append(url) or ADZUNA_RESPONSE,
    )

    query = parse_qs(urlparse(requested_urls[0]).query)
    assert query["app_id"] == ["test-app-id"]
    assert query["app_key"] == ["test-app-key"]
    assert query["what"] == ["accounting assistant"]
    assert query["where"] == ["Melbourne VIC"]
    assert jobs[0]["title"] == "Accounts Assistant"
    assert jobs[0]["url"] == "https://www.adzuna.com.au/details/123"
    assert jobs[0]["source"] == "Adzuna Australia"
    assert "reconcile" in jobs[0]["matches"]


def test_adzuna_defaults_to_most_frequent_candidate_keywords(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "test-app-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test-app-key")
    profile = {
        "keywords": ["sql", "python", "excel"],
        "keyword_counts": {"sql": 2, "python": 8, "excel": 4},
    }
    requested_urls = []

    scan_jobs_for_profile(
        profile,
        [{"name": "Adzuna", "source_type": "adzuna", "where": "Melbourne VIC"}],
        fetcher=lambda url: requested_urls.append(url) or ADZUNA_RESPONSE,
    )

    query = parse_qs(urlparse(requested_urls[0]).query)
    assert query["what"] == ["python excel sql"]


def test_adzuna_source_requires_credentials(monkeypatch):
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)

    with pytest.raises(ValueError, match="ADZUNA_APP_ID and ADZUNA_APP_KEY"):
        scan_jobs_for_profile(
            {"keywords": ["accounting"]},
            [{"source_type": "adzuna"}],
            fetcher=lambda url: "{}",
        )
