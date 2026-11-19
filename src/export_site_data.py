import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.job_report import run_daily_scan
from src.job_scanner import fetch_html
from src.profile_builder import analyze_job_fit
from src.resume_reader import process_resumes
from src.run_job_scan import PROJECT_ROOT, SOURCE_CONFIG, _load_cli_credentials, load_effective_profile
from src.store import DEFAULT_DB_PATH, _job_state


SITE_DATA_PATH = PROJECT_ROOT / "docs" / "data" / "jobs.json"
SYDNEY_TZ = ZoneInfo("Australia/Sydney")

_AI_ML_PATTERN = re.compile(
    r"\b(?:ai|ml|machine\s+learning|deep\s+learning|generative\s+ai|genai|llm|llms|nlp|computer\s+vision|mlops|applied\s+ai|data\s+scien(?:tist|ce))\b",
    re.IGNORECASE,
)
_DATA_ENG_PATTERN = re.compile(
    r"\b(?:data\s+engineer|analytics\s+engineer|etl|elt|dbt|snowflake|databricks|airflow|spark|kafka|data\s+platform|data\s+warehouse|pipeline)\b",
    re.IGNORECASE,
)
_BI_ANALYTICS_PATTERN = re.compile(
    r"\b(?:power\s*bi|powerbi|tableau|domo|business\s+intelligence|looker|qlik|bi\s+analyst|bi\s+developer|data\s+analyst|reporting|dashboard)\b",
    re.IGNORECASE,
)


def classify_role_track(job: dict) -> str:
    text = f"{job.get('title', '')} {job.get('category', '')} {job.get('description', '')[:400]}"
    title = str(job.get("title") or "")
    if _BI_ANALYTICS_PATTERN.search(title):
        return "BI & Analytics"
    if _AI_ML_PATTERN.search(title):
        return "AI & ML"
    if _DATA_ENG_PATTERN.search(title):
        return "Data Engineering"
    if _BI_ANALYTICS_PATTERN.search(text):
        return "BI & Analytics"
    if _AI_ML_PATTERN.search(text):
        return "AI & ML"
    if _DATA_ENG_PATTERN.search(text):
        return "Data Engineering"
    return "Data & Tech"


def _make_job_id(job: dict) -> str:
    basis = str(job.get("url") or f"{job.get('title', '')}|{job.get('company', '')}|{job.get('source', '')}")
    return hashlib.sha1(basis.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _truncate_clean(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rsplit(" ", 1)[0].rstrip(".,;:") + "…"


def deduplicate_matches(matches: list[dict]) -> list[dict]:
    by_key: dict[str, dict] = {}
    for job in matches:
        url = str(job.get("url") or "").strip().lower()
        title = str(job.get("title") or "").strip().lower()
        company = str(job.get("company") or "").strip().lower()
        key = url or f"{title}|{company}"
        existing = by_key.get(key)
        if existing is None or int(job.get("score", 0)) > int(existing.get("score", 0)):
            by_key[key] = job
    return sorted(
        by_key.values(),
        key=lambda item: (int(item.get("score", 0)), str(item.get("posted_at") or "")),
        reverse=True,
    )


def export_jobs_payload(
    profile: dict,
    sources: list[dict],
    fetcher=fetch_html,
    output_path: Path = SITE_DATA_PATH,
    db_path: Path | None = DEFAULT_DB_PATH,
) -> dict:
    adzuna_ready = bool(os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY"))
    active_sources = []
    source_warnings = []

    skipped_adzuna = 0
    for source in sources:
        if not source.get("enabled", True):
            continue
        if (source.get("source_type") or "").lower() == "adzuna" and not adzuna_ready:
            skipped_adzuna += 1
            continue
        active_sources.append(source)

    if skipped_adzuna:
        source_warnings.append(
            f"{skipped_adzuna} Adzuna source(s) skipped (add ADZUNA_APP_ID and ADZUNA_APP_KEY repository secrets to enable them)."
        )

    matches = run_daily_scan(
        profile,
        active_sources,
        fetcher=fetcher,
        db_path=db_path,
        source_error_callback=lambda err: source_warnings.append(f"{err.get('source')}: {err.get('error')}"),
    )

    deduped = deduplicate_matches(matches)
    source_counts: dict[str, int] = {}
    exported_jobs = []

    for job in deduped:
        source_name = str(job.get("source") or "Unknown")
        source_counts[source_name] = source_counts.get(source_name, 0) + 1
        fit = analyze_job_fit(profile, job)
        location = str(job.get("location") or "")
        state = _job_state(location)
        is_remote = bool(re.search(r"\b(?:remote|anywhere|hybrid)\b", f"{location} {job.get('title', '')}", re.IGNORECASE))
        description = str(job.get("description") or "")

        exported_jobs.append({
            "id": _make_job_id(job),
            "title": str(job.get("title") or "Untitled Job"),
            "company": str(job.get("company") or ""),
            "location": location or "Australia",
            "state": state or ("Remote" if is_remote else "AU"),
            "is_remote": is_remote,
            "track": classify_role_track(job),
            "source": source_name,
            "url": str(job.get("url") or ""),
            "posted_at": job.get("posted_at"),
            "score": int(job.get("score", 0)),
            "matches": list(job.get("matches") or []),
            "title_matches": list(job.get("title_matches") or []),
            "coverage_pct": fit.get("coverage_pct", 0),
            "missing_skills": fit.get("missing_skills", [])[:6],
            "fit_tip": fit["suggestions"][0] if fit.get("suggestions") else "",
            "salary_min": job.get("salary_min"),
            "salary_max": job.get("salary_max"),
            "contract_time": str(job.get("contract_time") or ""),
            "contract_type": str(job.get("contract_type") or ""),
            "summary": _truncate_clean(description, 280),
            "description": _truncate_clean(description, 1200),
        })

    now_utc = datetime.now(timezone.utc)
    now_sydney = now_utc.astimezone(SYDNEY_TZ)
    payload = {
        "generated_at": now_utc.isoformat(),
        "generated_at_aedt": now_sydney.strftime("%a %d %b %Y, %I:%M %p %Z"),
        "profile_name": profile.get("candidate_name", "Data & AI Profile"),
        "profile_keywords": list(profile.get("keywords") or []),
        "adzuna_enabled": adzuna_ready,
        "total_jobs": len(exported_jobs),
        "source_counts": dict(sorted(source_counts.items(), key=lambda item: (-item[1], item[0]))),
        "source_warnings": source_warnings,
        "jobs": exported_jobs,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    _load_cli_credentials()
    process_resumes()
    profile = load_effective_profile()
    sources = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
    payload = export_jobs_payload(profile, sources)
    print(f"Exported {payload['total_jobs']} relevant jobs to {SITE_DATA_PATH} at {payload['generated_at_aedt']}")
    if payload["source_warnings"]:
        print("Warnings:")
        for warning in payload["source_warnings"]:
            print(f"  - {warning}")


if __name__ == "__main__":
    main()
