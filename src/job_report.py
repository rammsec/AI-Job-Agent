from src.job_scanner import (
    build_search_key,
    calculate_max_days_old,
    score_jobs_for_profile,
    scan_jobs_for_profile,
)
from src.store import (
    create_database,
    get_latest_job_posted_at,
    get_source_scan_state,
    get_stored_jobs,
    save_candidate_profile,
    save_fetched_jobs,
    save_job_matches,
)


def build_report(matches: list[dict]) -> str:
    if not matches:
        return "No matching jobs found."

    lines = [
        "Daily Job Match Report",
        "======================",
    ]

    for index, match in enumerate(matches[:10], start=1):
        lines.append(f"{index}. {match.get('title', 'Untitled Job')} - Score: {match.get('score', 0)}")
        lines.append(f"   Source: {match.get('source', 'unknown')}")
        matched_keywords = ", ".join(match.get("matches", [])) or "no direct keyword overlap"
        lines.append(f"   Matches: {matched_keywords}")
        lines.append(f"   URL: {match.get('url', 'N/A')}")

    return "\n".join(lines)


def run_daily_scan(
    profile: dict,
    sources: list[dict],
    fetcher,
    db_path=None,
    source_error_callback=None,
) -> list[dict]:
    max_days_old_by_search_key = {}
    if db_path is not None:
        connection = create_database(db_path)
        connection.close()
        save_candidate_profile(db_path, profile)

        for source in sources:
            if not source.get("enabled", True) or (source.get("source_type") or "rss").lower() != "adzuna":
                continue
            search_key = build_search_key(source, profile)
            scan_state = get_source_scan_state(db_path, search_key)
            last_successful_scan_at = scan_state.get("last_successful_scan_at") if scan_state else None
            latest_posted_at = get_latest_job_posted_at(db_path, search_key)
            max_days_old_by_search_key[search_key] = max(
                calculate_max_days_old(
                    last_successful_scan_at,
                    initial_days=int(source.get("initial_days", 7)),
                    overlap_days=int(source.get("overlap_days", 1)),
                ),
                calculate_max_days_old(
                    latest_posted_at,
                    initial_days=int(source.get("initial_days", 7)),
                    overlap_days=int(source.get("overlap_days", 1)),
                ),
            )

    fetched_jobs_callback = None
    scan_completed_callback = None
    if db_path is not None:
        fetched_jobs_callback = lambda jobs: save_fetched_jobs(db_path, jobs)
        from src.store import mark_source_scan_success

        scan_completed_callback = lambda search_key, latest_posted_at, count: mark_source_scan_success(
            db_path,
            search_key,
            latest_posted_at,
            count,
        )

    matches = scan_jobs_for_profile(
        profile,
        sources,
        fetcher=fetcher,
        max_days_old_by_search_key=max_days_old_by_search_key,
        fetched_jobs_callback=fetched_jobs_callback,
        scan_completed_callback=scan_completed_callback,
        source_error_callback=source_error_callback,
    )

    if db_path is not None:
        stored_jobs = []
        domain = profile.get("domain") if isinstance(profile, dict) else None
        for source in sources:
            if not source.get("enabled", True):
                continue
            stored_jobs.extend(
                get_stored_jobs(
                    db_path,
                    build_search_key(source, profile),
                    target_only=bool(source.get("target_filter")),
                    domain=domain,
                )
            )
        matches = score_jobs_for_profile(profile, stored_jobs)
        save_job_matches(db_path, matches)

    return matches
