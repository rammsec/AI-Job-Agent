import json

from src.store import create_database, save_candidate_profile, save_job_matches, get_latest_profile, get_recent_matches


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
