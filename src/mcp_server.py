from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.job_filters import is_target_australia_job
from src.store import DEFAULT_DB_PATH, create_database, get_jobs_for_listing, save_fetched_jobs


def _resolve_db_path(db_path: str | Path | None) -> Path:
    resolved = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if not resolved.is_absolute():
        resolved = (Path.cwd() / resolved).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    create_database(resolved)
    return resolved


def _normalize_job(job: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(job, dict):
        raise ValueError("Job payload must be a dictionary.")

    normalized = dict(job)
    normalized.setdefault("source", "manual")
    normalized.setdefault("title", "Untitled Job")
    normalized.setdefault("description", "")
    normalized.setdefault("url", "")
    normalized.setdefault("company", "")
    normalized.setdefault("location", "")
    normalized.setdefault("posted_at", None)
    normalized.setdefault("category", "")
    normalized.setdefault("salary_min", None)
    normalized.setdefault("salary_max", None)
    normalized.setdefault("salary_is_predicted", None)
    normalized.setdefault("contract_time", "")
    normalized.setdefault("contract_type", "")
    raw_payload = normalized.get("raw_json")
    if raw_payload is None:
        raw_payload = {key: value for key, value in normalized.items() if key != "raw_json"}
    normalized["raw_json"] = raw_payload

    search_key = normalized.get("search_key")
    if not search_key:
        source_name = str(normalized.get("source", "manual")).strip() or "manual"
        title_text = str(normalized.get("title", "untitled-job")).strip() or "untitled-job"
        search_key = f"{source_name.lower()}::{title_text.lower().replace(' ', '-')[:60]}"
    normalized["search_key"] = search_key

    external_id = normalized.get("external_id")
    if not external_id:
        external_id = normalized.get("url") or normalized.get("title") or f"job-{len(normalized.get('raw_json', {}))}"
    normalized["external_id"] = str(external_id)
    return normalized


def add_job(job: dict[str, Any], db_path: str | Path | None = None) -> dict[str, Any]:
    """Add or update a single job record in the SQLite jobs table."""
    resolved_db = _resolve_db_path(db_path)
    normalized_job = _normalize_job(job)
    if not is_target_australia_job(normalized_job):
        raise ValueError("Only Australian business intelligence, Tableau, Power BI, and data engineering jobs can be saved.")
    save_fetched_jobs(resolved_db, [normalized_job])
    return {"saved": True, "job": normalized_job}


def search_jobs(
    query: str = "",
    db_path: str | Path | None = None,
    limit: int = 25,
    source: str | None = None,
) -> dict[str, Any]:
    """Search all stored jobs by title, company, location, description, or source."""
    resolved_db = _resolve_db_path(db_path)
    normalized_limit = max(1, int(limit or 25))
    jobs = get_jobs_for_listing(
        resolved_db,
        search_query=query or "",
        limit=normalized_limit,
        source_filter=source or None,
        target_only=True,
    )
    return {
        "jobs": jobs,
        "total": len(jobs),
        "query": query or "",
        "limit": normalized_limit,
        "source": source or None,
    }


def get_job_by_url(url: str, db_path: str | Path | None = None) -> dict[str, Any]:
    """Look up a stored job by its canonical URL."""
    resolved_db = _resolve_db_path(db_path)
    if not url:
        return {"found": False, "job": None}

    with sqlite3.connect(str(resolved_db)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM jobs WHERE LOWER(url) = LOWER(?) ORDER BY posted_at DESC, title ASC LIMIT 1",
            (url,),
        ).fetchone()

    if row is None:
        return {"found": False, "job": None}
    job = dict(row)
    if not is_target_australia_job(job):
        return {"found": False, "job": None}
    return {"found": True, "job": job}


def create_mcp_server(db_path: str | Path | None = None) -> FastMCP:
    """Create a lightweight MCP server exposing the job CRUD/search tools."""
    server = FastMCP("career-agent-job-tools")

    @server.tool(name="add_job")
    def add_job_tool(job: dict[str, Any]) -> dict[str, Any]:
        return add_job(job, db_path=db_path)

    @server.tool(name="search_jobs")
    def search_jobs_tool(query: str = "", limit: int = 25, source: str | None = None) -> dict[str, Any]:
        return search_jobs(query=query, db_path=db_path, limit=limit, source=source)

    @server.tool(name="get_job_by_url")
    def get_job_by_url_tool(url: str) -> dict[str, Any]:
        return get_job_by_url(url, db_path=db_path)

    return server


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the lightweight Career Agent MCP server.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="SQLite database file used for job persistence.")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio", help="MCP transport to run.")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    server = create_mcp_server(args.db_path)
    server.run(transport=args.transport)
