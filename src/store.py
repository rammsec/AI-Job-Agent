import json
import sqlite3
from pathlib import Path


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "career_agent.db"


def create_database(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_name TEXT,
            keywords_json TEXT,
            years_experience INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS job_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            matches_json TEXT,
            score INTEGER,
            source TEXT,
            url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    job_match_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(job_matches)").fetchall()
    }
    if "source" not in job_match_columns:
        connection.execute("ALTER TABLE job_matches ADD COLUMN source TEXT")
    if "url" not in job_match_columns:
        connection.execute("ALTER TABLE job_matches ADD COLUMN url TEXT")

    connection.commit()
    return connection


def save_candidate_profile(db_path: str | Path, profile: dict) -> None:
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(
            """
            INSERT INTO candidate_profiles (candidate_name, keywords_json, years_experience)
            VALUES (?, ?, ?)
            """,
            (
                profile.get("candidate_name", "Unknown"),
                json.dumps(profile.get("keywords", [])),
                int(profile.get("years_experience", 0)),
            ),
        )
        connection.commit()


def save_job_matches(db_path: str | Path, matches: list[dict]) -> None:
    with sqlite3.connect(str(db_path)) as connection:
        for match in matches:
            connection.execute(
                """
                INSERT INTO job_matches (title, matches_json, score, source, url)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    match.get("title", "Untitled Job"),
                    json.dumps(match.get("matches", [])),
                    int(match.get("score", 0)),
                    match.get("source"),
                    match.get("url"),
                ),
            )
        connection.commit()


def get_latest_profile(db_path: str | Path) -> dict:
    with sqlite3.connect(str(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT candidate_name, keywords_json, years_experience FROM candidate_profiles ORDER BY id DESC LIMIT 1"
        ).fetchone()

    if row is None:
        return {}

    return {
        "candidate_name": row["candidate_name"],
        "keywords_json": row["keywords_json"],
        "years_experience": row["years_experience"],
    }


def get_recent_matches(db_path: str | Path, limit: int = 10) -> list[dict]:
    with sqlite3.connect(str(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT title, matches_json, score, source, url FROM job_matches ORDER BY score DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    return [
        {
            "title": row["title"],
            "matches": json.loads(row["matches_json"]),
            "score": row["score"],
            "source": row["source"],
            "url": row["url"],
        }
        for row in rows
    ]
