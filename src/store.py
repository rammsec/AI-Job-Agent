import json
import re
import sqlite3
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

from bs4 import BeautifulSoup

from src.job_filters import is_target_australia_job


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "career_agent.db"
_AUSTRALIAN_STATE_CODES = ("ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA")
_CITY_STATE_LOOKUP = {
    "adelaide": "SA", "albury": "NSW", "ballarat": "VIC", "bendigo": "VIC",
    "brisbane": "QLD", "bunbury": "WA", "cairns": "QLD", "canberra": "ACT",
    "central coast": "NSW", "darwin": "NT", "geelong": "VIC", "gold coast": "QLD",
    "hobart": "TAS", "launceston": "TAS", "melbourne": "VIC", "newcastle": "NSW",
    "perth": "WA", "sunshine coast": "QLD", "sydney": "NSW", "townsville": "QLD",
    "wollongong": "NSW",
}
VALID_JOB_STATUSES = ("new", "shortlisted", "applied", "interviewing", "rejected", "archived")


def _clean_job_description(value: str | None) -> str:
    text = unescape(value or "")
    return " ".join(BeautifulSoup(text, "html.parser").get_text(" ", strip=True).split())


def _normalized_job_type(contract_time: str | None, contract_type: str | None) -> str:
    """Map source-specific employment labels to the listing's filter choices."""
    normalized_time = "".join(char for char in str(contract_time or "").lower() if char.isalnum())
    normalized_type = "".join(char for char in str(contract_type or "").lower() if char.isalnum())
    if normalized_time in {"fulltime", "full"}:
        return "Full time"
    if normalized_time in {"parttime", "part"}:
        return "Part time"
    if "contract" in normalized_type or "contract" in normalized_time:
        return "Contract"
    return ""


def _job_state(location: str | None) -> str:
    """Return an Australian state code from an explicit state or recognised city."""
    value = str(location or "")
    for state in _AUSTRALIAN_STATE_CODES:
        if re.search(rf"\b{state}\b", value, re.IGNORECASE):
            return state
    for city, state in _CITY_STATE_LOOKUP.items():
        if re.search(rf"\b{re.escape(city)}\b", value, re.IGNORECASE):
            return state
    return ""


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
        CREATE TABLE IF NOT EXISTS jobs (
            source TEXT NOT NULL,
            search_key TEXT NOT NULL,
            external_id TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            url TEXT,
            company TEXT,
            location TEXT,
            posted_at TEXT,
            category TEXT,
            salary_min REAL,
            salary_max REAL,
            salary_is_predicted TEXT,
            contract_time TEXT,
            contract_type TEXT,
            raw_json TEXT,
            search_terms TEXT NOT NULL DEFAULT '[]',
            application_status TEXT NOT NULL DEFAULT 'new',
            notes TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source, search_key, external_id)
        )
        """
    )

    job_columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()}
    if "search_terms" not in job_columns:
        connection.execute("ALTER TABLE jobs ADD COLUMN search_terms TEXT NOT NULL DEFAULT '[]'")
    if "application_status" not in job_columns:
        connection.execute("ALTER TABLE jobs ADD COLUMN application_status TEXT NOT NULL DEFAULT 'new'")
    if "notes" not in job_columns:
        connection.execute("ALTER TABLE jobs ADD COLUMN notes TEXT NOT NULL DEFAULT ''")

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS job_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS source_scan_state (
            search_key TEXT PRIMARY KEY,
            last_successful_scan_at TEXT NOT NULL,
            latest_posted_at TEXT,
            last_result_count INTEGER NOT NULL DEFAULT 0
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
            scan_id INTEGER,
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
    if "scan_id" not in job_match_columns:
        connection.execute("ALTER TABLE job_matches ADD COLUMN scan_id INTEGER")

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
        cursor = connection.execute("INSERT INTO job_scans DEFAULT VALUES")
        scan_id = cursor.lastrowid
        for match in matches:
            connection.execute(
                """
                INSERT INTO job_matches (title, matches_json, score, source, url, scan_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    match.get("title", "Untitled Job"),
                    json.dumps(match.get("matches", [])),
                    int(match.get("score", 0)),
                    match.get("source"),
                    match.get("url"),
                    scan_id,
                ),
            )
        connection.commit()


def save_fetched_jobs(db_path: str | Path, jobs: list[dict]) -> None:
    if not jobs:
        return

    with sqlite3.connect(str(db_path)) as connection:
        connection.executemany(
            """
            INSERT INTO jobs (
                source, search_key, external_id, title, description, url, company,
                location, posted_at, category, salary_min, salary_max,
                salary_is_predicted, contract_time, contract_type, raw_json,
                first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, search_key, external_id) DO UPDATE SET
                title = excluded.title,
                description = excluded.description,
                url = excluded.url,
                company = excluded.company,
                location = excluded.location,
                posted_at = excluded.posted_at,
                category = excluded.category,
                salary_min = excluded.salary_min,
                salary_max = excluded.salary_max,
                salary_is_predicted = excluded.salary_is_predicted,
                contract_time = excluded.contract_time,
                contract_type = excluded.contract_type,
                raw_json = excluded.raw_json,
                last_seen_at = excluded.last_seen_at
            """,
            [
                (
                    job.get("source", "unknown"),
                    job.get("search_key", ""),
                    str(job.get("external_id") or job.get("url") or job.get("title", "unknown")),
                    job.get("title", "Untitled Job"),
                    _clean_job_description(job.get("description", "")),
                    job.get("url", ""),
                    job.get("company", ""),
                    job.get("location", ""),
                    job.get("posted_at"),
                    job.get("category", ""),
                    job.get("salary_min"),
                    job.get("salary_max"),
                    job.get("salary_is_predicted"),
                    job.get("contract_time", ""),
                    job.get("contract_type", ""),
                    json.dumps(job.get("raw_json", job), ensure_ascii=False),
                    job.get("first_seen_at") or datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                )
                for job in jobs
            ],
        )
        connection.commit()


def save_job_if_changed(db_path: str | Path, job: dict) -> str:
    """Insert a job, update it when its details change, or skip an identical repeat."""
    source = str(job.get("source", "unknown"))
    search_key = str(job.get("search_key", ""))
    external_id = str(job.get("external_id") or job.get("url") or job.get("title", "unknown"))
    incoming_terms = job.get("search_terms") or []
    if isinstance(incoming_terms, str):
        try:
            incoming_terms = json.loads(incoming_terms)
        except json.JSONDecodeError:
            incoming_terms = [incoming_terms]
    if not isinstance(incoming_terms, (list, tuple, set)):
        incoming_terms = []
    incoming_terms = [str(term).strip() for term in incoming_terms if str(term).strip()]
    search_term = str(job.get("search_term") or "").strip()
    if search_term:
        incoming_terms.append(search_term)
    incoming_terms = list(dict.fromkeys(incoming_terms))
    values = {
        "title": job.get("title", "Untitled Job"),
        "description": _clean_job_description(job.get("description", "")),
        "url": job.get("url", ""),
        "company": job.get("company", ""),
        "location": job.get("location", ""),
        "posted_at": job.get("posted_at"),
        "category": job.get("category", ""),
        "salary_min": job.get("salary_min"),
        "salary_max": job.get("salary_max"),
        "salary_is_predicted": job.get("salary_is_predicted"),
        "contract_time": job.get("contract_time", ""),
        "contract_type": job.get("contract_type", ""),
        "search_terms": json.dumps(incoming_terms, ensure_ascii=False),
    }
    raw_json = json.dumps(job.get("raw_json", job), ensure_ascii=False)
    now = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(str(db_path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT title, description, url, company, location, posted_at, category,
                   salary_min, salary_max, salary_is_predicted, contract_time, contract_type,
                   search_terms
            FROM jobs
            WHERE source = ? AND search_key = ? AND external_id = ?
            """,
            (source, search_key, external_id),
        ).fetchone()

        if existing is None:
            connection.execute(
                """
                INSERT INTO jobs (
                    source, search_key, external_id, title, description, url, company,
                    location, posted_at, category, salary_min, salary_max,
                    salary_is_predicted, contract_time, contract_type, raw_json, search_terms,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source, search_key, external_id,
                    values["title"], values["description"], values["url"], values["company"],
                    values["location"], values["posted_at"], values["category"],
                    values["salary_min"], values["salary_max"], values["salary_is_predicted"],
                    values["contract_time"], values["contract_type"], raw_json,
                    values["search_terms"], now, now,
                ),
            )
            return "inserted"

        try:
            prior_terms = json.loads(existing[-1] or "[]")
        except (json.JSONDecodeError, TypeError):
            prior_terms = []
        if not isinstance(prior_terms, list):
            prior_terms = []
        merged_terms = list(dict.fromkeys(
            [str(term).strip() for term in [*prior_terms, *incoming_terms] if str(term).strip()]
        ))
        values["search_terms"] = json.dumps(merged_terms, ensure_ascii=False)
        existing_values = dict(zip(values, existing))
        if existing_values == values:
            return "skipped"

        connection.execute(
            """
            UPDATE jobs SET
                title = ?, description = ?, url = ?, company = ?, location = ?, posted_at = ?,
                category = ?, salary_min = ?, salary_max = ?, salary_is_predicted = ?,
                contract_time = ?, contract_type = ?, raw_json = ?, search_terms = ?, last_seen_at = ?
            WHERE source = ? AND search_key = ? AND external_id = ?
            """,
            (
                values["title"], values["description"], values["url"], values["company"],
                values["location"], values["posted_at"], values["category"],
                values["salary_min"], values["salary_max"], values["salary_is_predicted"],
                values["contract_time"], values["contract_type"], raw_json,
                values["search_terms"], now,
                source, search_key, external_id,
            ),
        )
        return "updated"


def update_job_status(
    db_path: str | Path,
    url: str,
    status: str,
    notes: str | None = None,
) -> bool:
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in VALID_JOB_STATUSES:
        raise ValueError(f"Status must be one of: {', '.join(VALID_JOB_STATUSES)}")
    if not url:
        raise ValueError("Job URL is required to update status.")

    create_database(db_path).close()
    with sqlite3.connect(str(db_path)) as connection:
        if notes is None:
            cursor = connection.execute(
                "UPDATE jobs SET application_status = ? WHERE LOWER(url) = LOWER(?)",
                (normalized_status, url.strip()),
            )
        else:
            cursor = connection.execute(
                "UPDATE jobs SET application_status = ?, notes = ? WHERE LOWER(url) = LOWER(?)",
                (normalized_status, str(notes).strip()[:4000], url.strip()),
            )
        connection.commit()
    return cursor.rowcount > 0


def get_latest_job_posted_at(db_path: str | Path, search_key: str) -> str | None:
    with sqlite3.connect(str(db_path)) as connection:
        row = connection.execute(
            "SELECT MAX(posted_at) FROM jobs WHERE search_key = ?",
            (search_key,),
        ).fetchone()
    return row[0] if row else None


def get_source_scan_state(db_path: str | Path, search_key: str) -> dict | None:
    with sqlite3.connect(str(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM source_scan_state WHERE search_key = ?",
            (search_key,),
        ).fetchone()
    return dict(row) if row else None


def mark_source_scan_success(
    db_path: str | Path,
    search_key: str,
    latest_posted_at: str | None,
    result_count: int,
    scanned_at: str | None = None,
) -> None:
    scanned_at = scanned_at or datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(
            """
            INSERT INTO source_scan_state (search_key, last_successful_scan_at, latest_posted_at, last_result_count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(search_key) DO UPDATE SET
                last_successful_scan_at = excluded.last_successful_scan_at,
                latest_posted_at = CASE
                    WHEN excluded.latest_posted_at IS NULL THEN source_scan_state.latest_posted_at
                    WHEN source_scan_state.latest_posted_at IS NULL THEN excluded.latest_posted_at
                    WHEN excluded.latest_posted_at > source_scan_state.latest_posted_at THEN excluded.latest_posted_at
                    ELSE source_scan_state.latest_posted_at
                END,
                last_result_count = excluded.last_result_count
            """,
            (search_key, scanned_at, latest_posted_at, result_count),
        )
        connection.commit()


def get_stored_jobs(
    db_path: str | Path,
    search_key: str | None = None,
    target_only: bool = False,
    domain: str | None = None,
) -> list[dict]:
    create_database(db_path).close()
    with sqlite3.connect(str(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        if search_key is None:
            rows = connection.execute("SELECT * FROM jobs ORDER BY posted_at DESC, title").fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE search_key = ? ORDER BY posted_at DESC, title",
                (search_key,),
            ).fetchall()
    jobs = [dict(row) for row in rows]
    for job in jobs:
        job["description"] = _clean_job_description(job.get("description"))
    if target_only:
        jobs = [job for job in jobs if is_target_australia_job(job, domain=domain)]
    return jobs


def get_jobs_for_listing(
    db_path: str | Path,
    search_query: str = "",
    limit: int = 25,
    source_filter: str | None = None,
    region_filter: str | None = None,
    state_filter: str | None = None,
    job_type_filter: str | None = None,
    status_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    loaded_from: str | None = None,
    loaded_to: str | None = None,
    target_only: bool = False,
    domain: str | None = None,
    include_all: bool = False,
) -> list[dict]:
    create_database(db_path).close()
    normalized_query = (search_query or "").strip()
    normalized_limit = max(1, int(limit or 25))

    where_clauses = []
    params: list[str | int] = []

    if normalized_query:
        where_clauses.append(
            "("
            "LOWER(title) LIKE '%' || LOWER(?) || '%' OR "
            "LOWER(company) LIKE '%' || LOWER(?) || '%' OR "
            "LOWER(location) LIKE '%' || LOWER(?) || '%' OR "
            "LOWER(description) LIKE '%' || LOWER(?) || '%' OR "
            "LOWER(source) LIKE '%' || LOWER(?) || '%' OR "
            "LOWER(search_terms) LIKE '%' || LOWER(?) || '%'"
            ")"
        )
        params.extend([normalized_query] * 6)

    if source_filter:
        where_clauses.append("LOWER(source) = LOWER(?)")
        params.append(source_filter)

    if region_filter:
        where_clauses.append("LOWER(location) LIKE '%' || LOWER(?) || '%'")
        params.append(region_filter)

    if status_filter:
        where_clauses.append("LOWER(application_status) = LOWER(?)")
        params.append(status_filter.strip())

    normalized_type = (job_type_filter or "").strip().lower()
    if normalized_type == "full time":
        where_clauses.append("REPLACE(REPLACE(REPLACE(LOWER(contract_time), '_', ''), ' ', ''), '-', '') IN ('fulltime', 'full')")
    elif normalized_type == "part time":
        where_clauses.append("REPLACE(REPLACE(REPLACE(LOWER(contract_time), '_', ''), ' ', ''), '-', '') IN ('parttime', 'part')")
    elif normalized_type == "contract":
        where_clauses.append("LOWER(contract_time) LIKE '%contract%' OR LOWER(contract_type) LIKE '%contract%'")

    if date_from:
        where_clauses.append("date(posted_at) >= date(?)")
        params.append(date_from)

    if date_to:
        where_clauses.append("date(posted_at) <= date(?)")
        params.append(date_to)

    if loaded_from:
        where_clauses.append("date(first_seen_at) >= date(?)")
        params.append(loaded_from)

    if loaded_to:
        where_clauses.append("date(first_seen_at) <= date(?)")
        params.append(loaded_to)

    query = """
        SELECT source, search_key, external_id, title, company, location, description, url,
               posted_at, first_seen_at, category, salary_min, salary_max, contract_time,
               contract_type, search_terms, application_status, notes
        FROM jobs
    """
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)
    query += " ORDER BY posted_at DESC, title ASC"
    if not include_all and not target_only and not state_filter:
        query += " LIMIT ?"
        params.append(normalized_limit)

    with sqlite3.connect(str(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, tuple(params)).fetchall()

    jobs = [dict(row) for row in rows]
    for job in jobs:
        job["description"] = _clean_job_description(job.get("description"))
        job["job_type"] = _normalized_job_type(job.get("contract_time"), job.get("contract_type"))
        job["state"] = _job_state(job.get("location"))
        job["application_status"] = job.get("application_status") or "new"
        job["notes"] = job.get("notes") or ""
    if state_filter:
        jobs = [job for job in jobs if job["state"] == state_filter.strip().upper()]
    if target_only:
        jobs = [job for job in jobs if is_target_australia_job(job, domain=domain)]
        if not include_all:
            jobs = jobs[:normalized_limit]
    elif state_filter and not include_all:
        jobs = jobs[:normalized_limit]
    return jobs


def get_job_listing_options(db_path: str | Path, target_only: bool = False) -> dict:
    create_database(db_path).close()
    with sqlite3.connect(str(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT source, title, description, location, category, contract_time, contract_type, application_status FROM jobs"
        ).fetchall()

    jobs = [dict(row) for row in rows]
    if target_only:
        jobs = [job for job in jobs if is_target_australia_job(job)]
    return {
        "sources": sorted({job["source"] for job in jobs if job.get("source")}),
        "regions": sorted({job["location"] for job in jobs if job.get("location")}),
        "states": sorted({_job_state(job.get("location")) for job in jobs if _job_state(job.get("location"))}),
        "job_types": sorted({_normalized_job_type(job.get("contract_time"), job.get("contract_type")) for job in jobs if _normalized_job_type(job.get("contract_time"), job.get("contract_type"))}),
        "statuses": list(VALID_JOB_STATUSES),
    }


def prune_jobs_to_target_scope(db_path: str | Path = DEFAULT_DB_PATH) -> int:
    create_database(db_path).close()
    with sqlite3.connect(str(db_path)) as connection:
        rows = connection.execute(
            "SELECT source, search_key, external_id, title, description, location, category FROM jobs"
        ).fetchall()
        rows_to_delete = [
            (row[0], row[1], row[2])
            for row in rows
            if not is_target_australia_job({
                "title": row[3],
                "description": _clean_job_description(row[4]),
                "location": row[5],
                "category": row[6],
            })
        ]
        connection.executemany(
            "DELETE FROM jobs WHERE source = ? AND search_key = ? AND external_id = ?",
            rows_to_delete,
        )
        connection.execute(
            """
            DELETE FROM job_matches
            WHERE url IS NULL OR url = '' OR url NOT IN (
                SELECT url FROM jobs WHERE url IS NOT NULL AND url <> ''
            )
            """
        )
        connection.commit()
    return len(rows_to_delete)


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
        latest_scan = connection.execute("SELECT id FROM job_scans ORDER BY id DESC LIMIT 1").fetchone()
        if latest_scan is None:
            rows = connection.execute(
                "SELECT title, matches_json, score, source, url FROM job_matches ORDER BY score DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT title, matches_json, score, source, url
                FROM job_matches
                WHERE scan_id = ?
                ORDER BY score DESC, id DESC
                LIMIT ?
                """,
                (latest_scan["id"], limit),
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


def has_job_scans(db_path: str | Path) -> bool:
    with sqlite3.connect(str(db_path)) as connection:
        return connection.execute("SELECT EXISTS(SELECT 1 FROM job_scans)").fetchone()[0] == 1
