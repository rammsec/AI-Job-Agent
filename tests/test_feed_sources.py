from urllib.parse import parse_qs, urlparse

import pytest

from src.job_scanner import (
  calculate_max_days_old,
  parse_feed_entries,
  scan_jobs_for_profile,
)


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


def test_parse_rss_preserves_guid_date_and_explicit_location():
    feed = """<?xml version="1.0"?>
    <rss version="2.0"><channel><item>
      <guid>posting-1</guid><title>Data Engineer</title>
      <link>https://example.com/jobs/1</link>
      <pubDate>Mon, 05 Oct 2026 10:00:00 +0000</pubDate>
      <category>Data Engineering</category>
      <location>Australia</location>
      <description>Build data platforms for Australian teams.</description>
    </item></channel></rss>"""

    job = parse_feed_entries(feed, "rss")[0]

    assert job["external_id"] == "posting-1"
    assert job["posted_at"] == "2026-10-05T10:00:00+00:00"
    assert job["location"] == "Australia"
    assert job["category"] == "Data Engineering"


def test_parse_json_supports_data_jobs_and_provider_field_names():
    import json

    arbeitnow = json.dumps({"data": [{
        "slug": "data-engineer-au",
        "title": "Data Engineer",
        "description": "Build data platforms",
        "url": "https://example.com/arbeitnow/1",
        "company_name": "Example AU",
        "location": "Australia",
        "created_at": "2026-10-05T10:00:00Z",
        "tags": ["data", "engineering"],
    }]})
    remotive = json.dumps({"jobs": [{
        "id": 42,
        "title": "Power BI Analyst",
        "description": "Build Power BI dashboards",
        "url": "https://example.com/remotive/42",
        "company_name": "Remote Co",
        "candidate_required_location": "Australia",
        "publication_date": "2026-10-05T10:00:00",
        "category": "Data Analysis",
    }]})
    jobicy = json.dumps({"jobs": [{
        "id": 43,
        "jobTitle": "Tableau Developer",
        "jobDescription": "Develop Tableau reporting",
        "url": "https://example.com/jobicy/43",
        "companyName": "Jobicy Co",
        "jobGeo": "Australia",
        "pubDate": "2026-10-05T10:00:00",
        "jobIndustry": ["Data"],
    }]})

    parsed = [
        parse_feed_entries(payload, "json")[0]
        for payload in (arbeitnow, remotive, jobicy)
    ]

    assert [job["title"] for job in parsed] == ["Data Engineer", "Power BI Analyst", "Tableau Developer"]
    assert [job["location"] for job in parsed] == ["Australia", "Australia", "Australia"]
    assert [job["external_id"] for job in parsed] == ["data-engineer-au", "42", "43"]
    assert parsed[1]["company"] == "Remote Co"


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


def test_scan_json_feed_uses_configured_items_path_and_target_filter():
    import json

    payload = json.dumps({"data": [
        {
            "slug": "tableau-australia",
            "title": "Tableau Developer",
            "description": "Build Tableau dashboards for the data team.",
            "url": "https://example.com/jobs/tableau-australia",
            "company_name": "Example Australia",
            "location": "Sydney NSW",
            "tags": ["analytics"],
        },
        {
            "slug": "tableau-uk",
            "title": "Tableau Developer",
            "description": "Build Tableau dashboards.",
            "url": "https://example.com/jobs/tableau-uk",
            "company_name": "Example UK",
            "location": "London, UK",
        },
    ]})
    persisted = []

    matches = scan_jobs_for_profile(
        {"keywords": ["tableau"]},
        [{
            "name": "Example JSON board",
            "source_type": "json",
            "feed_url": "https://example.com/api/jobs",
            "items_path": "data",
            "target_filter": True,
        }],
        fetcher=lambda url: payload,
        fetched_jobs_callback=persisted.extend,
    )

    assert len(matches) == 1
    assert len(persisted) == 1
    assert persisted[0]["external_id"] == "tableau-australia"
    assert persisted[0]["company"] == "Example Australia"


def test_scan_jobs_for_profile_normalizes_greenhouse_and_lever_sources():
    import json

    payloads = {
        "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true": json.dumps({
            "jobs": [{
                "id": 123,
                "title": "Senior SQL Analyst",
                "content": "<p>Build SQL reporting and data analysis.</p>",
                "absolute_url": "https://boards.greenhouse.io/example/jobs/123",
                "updated_at": "2026-10-01T09:00:00Z",
                "location": {"name": "Melbourne VIC"},
                "departments": [{"name": "Data"}],
            }]
        }),
        "https://api.lever.co/v0/postings/leverdemo?mode=json&skip=0&limit=100": json.dumps([{
            "id": "lever-456",
            "text": "Data Reporting Analyst",
            "descriptionPlain": "Create Excel and SQL reports.",
            "hostedUrl": "https://jobs.lever.co/leverdemo/lever-456",
            "categories": {"location": "Melbourne VIC", "team": "Analytics"},
            "createdAt": 1790845200000,
        }]),
    }
    sources = [
        {
            "name": "Greenhouse example",
            "source_type": "greenhouse",
            "board_token": "example",
        },
        {
            "name": "Lever demo",
            "source_type": "lever",
            "site": "leverdemo",
        },
    ]
    fetched = []

    jobs = scan_jobs_for_profile(
        {"keywords": ["sql", "data", "reporting"]},
        sources,
        fetcher=lambda url: payloads[url],
        fetched_jobs_callback=fetched.extend,
    )

    assert len(jobs) == 2
    greenhouse_job = next(job for job in jobs if job["source"] == "Greenhouse example")
    lever_job = next(job for job in jobs if job["source"] == "Lever demo")
    assert greenhouse_job["url"] == "https://boards.greenhouse.io/example/jobs/123"
    assert greenhouse_job["location"] == "Melbourne VIC"
    assert "SQL reporting" in greenhouse_job["description"]
    assert lever_job["company"] == "Lever demo"
    assert lever_job["location"] == "Melbourne VIC"
    assert next(job for job in fetched if job["source"] == "Lever demo")["external_id"] == "lever-456"


def test_scan_continues_when_one_source_fails(monkeypatch):
    import json

    monkeypatch.setenv("ADZUNA_APP_ID", "test-app-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test-app-key")
    errors = []
    fetched = []
    source_payload = json.dumps({
        "jobs": [{
            "id": 701,
            "title": "SQL Data Analyst",
            "content": "SQL reporting and analysis",
            "absolute_url": "https://boards.greenhouse.io/example/jobs/701",
            "location": {"name": "Melbourne VIC"},
        }]
    })

    def fetcher(url):
        if "adzuna" in url:
            raise RuntimeError("Adzuna authorization failed")
        return source_payload

    jobs = scan_jobs_for_profile(
        {"keywords": ["sql", "data"]},
        [
            {
                "name": "Unavailable Adzuna",
                "source_type": "adzuna",
                "country": "au",
                "what": "sql analyst",
            },
            {
                "name": "Greenhouse example",
                "source_type": "greenhouse",
                "board_token": "example",
            },
        ],
        fetcher=fetcher,
        fetched_jobs_callback=fetched.extend,
        source_error_callback=errors.append,
    )

    assert len(jobs) == 1
    assert len(fetched) == 1
    assert errors == [{"source": "Unavailable Adzuna", "error": "Adzuna authorization failed"}]


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


    def test_adzuna_requests_page_and_date_window(monkeypatch):
      monkeypatch.setenv("ADZUNA_APP_ID", "test-app-id")
      monkeypatch.setenv("ADZUNA_APP_KEY", "test-app-key")
      requested_urls = []
      response = json.dumps({"count": 0, "results": []})

      scan_jobs_for_profile(
        {"keywords": ["sql"]},
        [{"name": "Adzuna", "source_type": "adzuna", "initial_days": 7}],
        fetcher=lambda url: requested_urls.append(url) or response,
      )

      query = parse_qs(urlparse(requested_urls[0]).query)
      assert urlparse(requested_urls[0]).path.endswith("/search/1")
      assert query["max_days_old"] == ["7"]
      assert query["sort_by"] == ["date"]
      assert query["sort_dir"] == ["down"]


    def test_calculate_max_days_old_uses_initial_window_then_overlapping_watermark():
      from datetime import datetime, timezone

      now = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)

      assert calculate_max_days_old(None, now=now) == 7
      assert calculate_max_days_old("2026-09-28T12:00:00Z", now=now) == 2
      assert calculate_max_days_old("invalid-date", initial_days=7, now=now) == 7


    def test_adzuna_fetch_paginates_until_api_count_is_reached(monkeypatch):
      monkeypatch.setenv("ADZUNA_APP_ID", "test-app-id")
      monkeypatch.setenv("ADZUNA_APP_KEY", "test-app-key")
      page_size = 2
      requested_urls = []
      pages = {
        1: {
          "count": 3,
          "results": [
            {"id": "1", "title": "SQL Analyst", "description": "SQL reports", "created": "2026-09-29T08:00:00Z"},
            {"id": "2", "title": "Data Analyst", "description": "Data reports", "created": "2026-09-29T07:00:00Z"},
          ],
        },
        2: {
          "count": 3,
          "results": [
            {"id": "3", "title": "SQL Developer", "description": "Database work", "created": "2026-09-28T07:00:00Z"},
          ],
        },
      }

      def fetch_page(url):
        requested_urls.append(url)
        page = int(urlparse(url).path.rsplit("/", 1)[1])
        return json.dumps(pages[page])

      jobs = scan_jobs_for_profile(
        {"keywords": ["sql", "data"]},
        [{
          "name": "Adzuna",
          "source_type": "adzuna",
          "results_per_page": page_size,
          "max_pages_per_scan": 3,
        }],
        fetcher=fetch_page,
      )

      assert len(requested_urls) == 2
      assert len(jobs) == 3
      assert jobs[0]["posted_at"] == "2026-09-29T08:00:00Z"
