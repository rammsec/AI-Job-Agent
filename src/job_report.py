from src.job_scanner import scan_jobs_for_profile
from src.store import create_database, save_candidate_profile, save_job_matches, get_recent_matches


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


def run_daily_scan(profile: dict, sources: list[dict], fetcher, db_path=None) -> list[dict]:
    matches = scan_jobs_for_profile(profile, sources, fetcher=fetcher)

    if db_path is not None:
        create_database(db_path)
        save_candidate_profile(db_path, profile)
        save_job_matches(db_path, matches)

    return matches
