import pytest

from src.job_report import build_report, run_daily_scan
from src.store import get_source_scan_state, get_stored_jobs


PROFILE = {
    "candidate_name": "Sample Candidate",
    "keywords": ["python", "fastapi", "azure", "sql"],
    "years_experience": 5,
}


SOURCES = [
    {
        "name": "demo-rss",
        "source_type": "rss",
        "feed_url": "https://example.com/rss",
    }
]

RSS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Senior Python Developer</title>
      <link>https://example.com/jobs/1</link>
      <description>FastAPI, Azure, SQL and Python experience</description>
    </item>
    <item>
      <title>Data Analyst</title>
      <link>https://example.com/jobs/2</link>
      <description>Excel, reporting, Power BI</description>
    </item>
  </channel>
</rss>
"""


def test_build_report_formats_top_matches():
    matches = [
        {"title": "Senior Python Developer", "score": 90, "matches": ["python", "fastapi", "azure"]},
        {"title": "Data Analyst", "score": 40, "matches": ["sql"]},
    ]

    report = build_report(matches)

    assert "Senior Python Developer" in report
    assert "90" in report
    assert "python" in report.lower()


def test_run_daily_scan_persists_and_returns_matches(tmp_path):
    db_path = tmp_path / "daily_scan.db"
    matches = run_daily_scan(
        PROFILE,
        SOURCES,
        fetcher=lambda url: RSS_FEED,
        db_path=db_path,
    )

    assert matches[0]["title"] == "Senior Python Developer"
    assert matches[0]["score"] > 0


def test_daily_scan_initial_backfill_persists_unmatched_ads_then_reuses_watermark(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import json
    from urllib.parse import parse_qs, urlparse

    monkeypatch.setenv("ADZUNA_APP_ID", "test-app-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test-app-key")
    db_path = tmp_path / "incremental.db"
    source = [{
        "name": "Adzuna Australia",
        "source_type": "adzuna",
        "what": "python",
        "where": "Melbourne VIC",
        "results_per_page": 20,
    }]
    newest_date = datetime.now(timezone.utc).isoformat()
    older_date = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    first_payload = {"count": 2, "results": [
        {
            "id": "job-relevant",
            "title": "Python Engineer",
            "description": "Build APIs",
            "created": newest_date,
            "redirect_url": "https://example.com/relevant",
            "company": {"display_name": "Example Tech"},
            "location": {"display_name": "Melbourne VIC"},
            "category": {"label": "IT"},
            "salary_min": 100000,
        },
        {
            "id": "job-unmatched",
            "title": "Sales Coordinator",
            "description": "Customer relationships",
            "created": older_date,
            "redirect_url": "https://example.com/unmatched",
        },
    ]}
    observed_windows = []

    def first_fetch(url):
        observed_windows.append(parse_qs(urlparse(url).query)["max_days_old"][0])
        return json.dumps(first_payload)

    matches = run_daily_scan(
        {"candidate_name": "Test Candidate", "keywords": ["python"], "years_experience": 6},
        source,
        fetcher=first_fetch,
        db_path=db_path,
    )

    stored_jobs = get_stored_jobs(db_path)
    assert observed_windows == ["7"]
    assert len(stored_jobs) == 2
    assert len(matches) == 1
    assert stored_jobs[1]["company"] == "Example Tech" or stored_jobs[0]["company"] == "Example Tech"

    second_payload = {"count": 1, "results": [{
        **first_payload["results"][0],
        "id": "job-new",
        "title": "Senior Python Engineer",
        "created": datetime.now(timezone.utc).isoformat(),
        "redirect_url": "https://example.com/new",
    }]}

    def second_fetch(url):
        observed_windows.append(parse_qs(urlparse(url).query)["max_days_old"][0])
        return json.dumps(second_payload)

    second_matches = run_daily_scan(
        {"candidate_name": "Test Candidate", "keywords": ["python"], "years_experience": 6},
        source,
        fetcher=second_fetch,
        db_path=db_path,
    )

    assert int(observed_windows[1]) < 7
    assert len(get_stored_jobs(db_path)) == 3
    assert {match["title"] for match in second_matches} == {
        "Python Engineer",
        "Senior Python Engineer",
    }


def test_incomplete_pagination_does_not_advance_scan_checkpoint(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("ADZUNA_APP_ID", "test-app-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test-app-key")
    db_path = tmp_path / "incomplete.db"
    source = [{
        "name": "Adzuna Australia",
        "source_type": "adzuna",
        "what": "python",
        "results_per_page": 1,
        "max_pages_per_scan": 1,
    }]

    with pytest.raises(RuntimeError, match="page limit"):
        run_daily_scan(
            {"keywords": ["python"]},
            source,
            fetcher=lambda url: json.dumps({
                "count": 2,
                "results": [{
                    "id": "job-1",
                    "title": "Python Engineer",
                    "description": "Python work",
                    "created": "2026-09-29T10:00:00Z",
                }],
            }),
            db_path=db_path,
        )

    from src.job_scanner import build_search_key

    search_key = build_search_key(source[0], {"keywords": ["python"]})
    assert get_source_scan_state(db_path, search_key) is None
    assert get_stored_jobs(db_path, search_key) == []
