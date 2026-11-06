import json

from src.store import (
    create_database,
    get_latest_job_posted_at,
    get_latest_profile,
    get_job_listing_options,
    get_jobs_for_listing,
    get_recent_matches,
    get_stored_jobs,
    has_job_scans,
    prune_jobs_to_target_scope,
    save_candidate_profile,
    save_fetched_jobs,
    save_job_matches,
)


PROFILE = {
    "candidate_name": "Sample Candidate",
    "keywords": ["python", "fastapi", "azure", "sql"],
    "years_experience": 5,
}

MATCHES = [
    {
        "title": "Senior Python Developer",
        "score": 90,
        "matches": ["python", "fastapi", "azure"],
        "source": "Adzuna Australia",
        "url": "https://example.com/job/1",
    },
    {"title": "Data Analyst", "score": 50, "matches": ["sql"]},
]


def test_store_roundtrip_profile_and_matches(tmp_path):
    db_path = tmp_path / "career_agent.db"
    create_database(db_path)

    save_candidate_profile(db_path, PROFILE)
    save_job_matches(db_path, MATCHES)

    stored_profile = get_latest_profile(db_path)
    stored_matches = get_recent_matches(db_path)

    assert stored_profile["candidate_name"] == "Sample Candidate"
    assert json.loads(stored_profile["keywords_json"]) == PROFILE["keywords"]
    assert stored_matches[0]["title"] == "Senior Python Developer"
    assert stored_matches[0]["score"] == 90
    assert stored_matches[0]["source"] == "Adzuna Australia"
    assert stored_matches[0]["url"] == "https://example.com/job/1"


def test_database_upgrades_existing_job_match_table(tmp_path):
    import sqlite3

    db_path = tmp_path / "existing.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE job_matches (id INTEGER PRIMARY KEY, title TEXT, matches_json TEXT, score INTEGER, created_at TEXT)"
        )

    create_database(db_path)
    save_job_matches(db_path, MATCHES[:1])

    assert get_recent_matches(db_path)[0]["url"] == "https://example.com/job/1"


def test_recent_matches_only_returns_latest_scan_and_preserves_history(tmp_path):
    db_path = tmp_path / "career_agent.db"
    create_database(db_path)

    save_job_matches(db_path, MATCHES[:1])
    save_job_matches(db_path, [])

    assert get_recent_matches(db_path) == []
    assert has_job_scans(db_path) is True
    with create_database(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM job_matches").fetchone()[0] == 1


def test_fetched_jobs_upsert_details_and_latest_posted_lookup(tmp_path):
    db_path = tmp_path / "career_agent.db"
    create_database(db_path).close()
    fetched = [{
        "source": "Adzuna Australia",
        "search_key": "search-sha",
        "external_id": "ad-100",
        "title": "Senior SQL Analyst",
        "description": "Analyse finance data",
        "url": "https://example.com/jobs/100",
        "company": "Example Ltd",
        "location": "Melbourne VIC",
        "posted_at": "2026-09-29T10:00:00Z",
        "category": "IT Jobs",
        "salary_min": 90000,
        "salary_max": 110000,
        "contract_type": "permanent",
        "raw_json": {"id": "ad-100"},
    }]

    save_fetched_jobs(db_path, fetched)
    fetched[0]["title"] = "Senior SQL Analyst Updated"
    save_fetched_jobs(db_path, fetched)

    stored = get_stored_jobs(db_path, "search-sha")
    assert len(stored) == 1
    assert stored[0]["title"] == "Senior SQL Analyst Updated"
    assert stored[0]["company"] == "Example Ltd"
    assert stored[0]["salary_min"] == 90000
    assert get_latest_job_posted_at(db_path, "search-sha") == "2026-09-29T10:00:00Z"


def test_job_listing_returns_readable_text_for_html_descriptions(tmp_path):
    db_path = tmp_path / "career_agent.db"
    create_database(db_path).close()
    save_fetched_jobs(db_path, [{
        "source": "Culture Amp (Greenhouse)",
        "search_key": "greenhouse-search",
        "external_id": "gh-100",
        "title": "Senior Data Analyst",
        "description": '<div class="content-intro"><p><strong>Build SQL reports.</strong></p><p>Support data analysis.</p></div>',
        "url": "https://example.com/jobs/100",
    }])

    jobs = get_jobs_for_listing(db_path)

    assert jobs[0]["description"] == "Build SQL reports. Support data analysis."


def test_target_listing_and_prune_keep_only_requested_australian_jobs(tmp_path):
    db_path = tmp_path / "career_agent.db"
    create_database(db_path).close()
    save_fetched_jobs(db_path, [
        {
            "source": "Test",
            "search_key": "test",
            "external_id": "au-target",
            "title": "Power BI Engineer",
            "description": "Build reports",
            "location": "Sydney NSW",
            "url": "https://example.com/au-target",
        },
        {
            "source": "Test",
            "search_key": "test",
            "external_id": "overseas-target",
            "title": "Tableau Developer",
            "location": "London",
            "url": "https://example.com/overseas-target",
        },
        {
            "source": "Test",
            "search_key": "test",
            "external_id": "au-unrelated",
            "title": "Business Analyst",
            "location": "Melbourne VIC",
            "url": "https://example.com/au-unrelated",
        },
    ])

    assert [job["title"] for job in get_jobs_for_listing(db_path, target_only=True)] == ["Power BI Engineer"]
    assert prune_jobs_to_target_scope(db_path) == 2
    assert [job["external_id"] for job in get_stored_jobs(db_path)] == ["au-target"]


def test_job_listing_filters_source_region_and_date_range(tmp_path):
    db_path = tmp_path / "career_agent.db"
    create_database(db_path).close()
    save_fetched_jobs(db_path, [
        {
            "source": "Greenhouse Board",
            "search_key": "filter-test",
            "external_id": "job-sydney-new",
            "title": "Power BI Engineer",
            "location": "Sydney NSW",
            "posted_at": "2026-10-02T09:00:00Z",
        },
        {
            "source": "Greenhouse Board",
            "search_key": "filter-test",
            "external_id": "job-sydney-old",
            "title": "Data Analyst",
            "location": "Sydney NSW",
            "posted_at": "2026-09-29T09:00:00Z",
        },
        {
            "source": "Lever Board",
            "search_key": "filter-test",
            "external_id": "job-melbourne-new",
            "title": "Data Scientist",
            "location": "Melbourne VIC",
            "posted_at": "2026-10-02T12:00:00Z",
        },
    ])

    jobs = get_jobs_for_listing(
        db_path,
        source_filter="Greenhouse Board",
        region_filter="Sydney",
        date_from="2026-10-01",
        date_to="2026-10-03",
        target_only=True,
    )

    assert [job["title"] for job in jobs] == ["Power BI Engineer"]


def test_job_listing_filters_by_when_jobs_were_loaded(tmp_path):
    db_path = tmp_path / "career_agent.db"
    create_database(db_path).close()
    save_fetched_jobs(db_path, [
        {
            "source": "Test source", "search_key": "loaded-filter", "external_id": "older",
            "title": "Older loaded job", "first_seen_at": "2026-10-01T09:00:00+00:00",
        },
        {
            "source": "Test source", "search_key": "loaded-filter", "external_id": "newer",
            "title": "Newer loaded job", "first_seen_at": "2026-10-10T09:00:00+00:00",
        },
    ])

    jobs = get_jobs_for_listing(db_path, loaded_from="2026-10-10", loaded_to="2026-10-10")

    assert [job["title"] for job in jobs] == ["Newer loaded job"]
    assert jobs[0]["first_seen_at"] == "2026-10-10T09:00:00+00:00"


def test_job_listing_filters_by_normalized_job_type_and_exposes_options(tmp_path):
    db_path = tmp_path / "career_agent.db"
    create_database(db_path).close()
    save_fetched_jobs(db_path, [
        {"source": "Test", "search_key": "type", "external_id": "full", "title": "Full time", "contract_time": "full_time"},
        {"source": "Test", "search_key": "type", "external_id": "part", "title": "Part time", "contract_time": "Part-time"},
        {"source": "Test", "search_key": "type", "external_id": "contract", "title": "Contract", "contract_type": "contract"},
    ])

    assert [job["title"] for job in get_jobs_for_listing(db_path, job_type_filter="Part time")] == ["Part time"]
    assert get_job_listing_options(db_path)["job_types"] == ["Contract", "Full time", "Part time"]


def test_job_listing_calculates_state_from_city_and_filters_by_state(tmp_path):
    db_path = tmp_path / "career_agent.db"
    create_database(db_path).close()
    save_fetched_jobs(db_path, [
        {"source": "Test", "search_key": "state", "external_id": "melbourne", "title": "Melbourne role", "location": "Melbourne"},
        {"source": "Test", "search_key": "state", "external_id": "brisbane", "title": "Brisbane role", "location": "Brisbane"},
    ])

    jobs = get_jobs_for_listing(db_path, state_filter="VIC")

    assert [job["title"] for job in jobs] == ["Melbourne role"]
    assert jobs[0]["state"] == "VIC"
    assert get_job_listing_options(db_path)["states"] == ["QLD", "VIC"]
