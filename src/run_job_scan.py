import json
from pathlib import Path

from src.job_report import build_report, run_daily_scan
from src.job_scanner import fetch_html
from src.profile_builder import extract_candidate_profile
from src.store import DEFAULT_DB_PATH


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESUME_TEXT_FOLDER = PROJECT_ROOT / "data" / "output" / "text"
SOURCE_CONFIG = PROJECT_ROOT / "data" / "job_sources.json"


def load_latest_candidate_profile(text_folder: Path = RESUME_TEXT_FOLDER) -> dict:
    resume_text_files = sorted(
        text_folder.glob("*.txt"),
        key=lambda file_path: file_path.stat().st_mtime,
        reverse=True,
    )
    if not resume_text_files:
        raise FileNotFoundError(
            f"No extracted resume text found in {text_folder}. Run the resume ingestion first."
        )

    resume_path = resume_text_files[0]
    profile = extract_candidate_profile(resume_path.read_text(encoding="utf-8", errors="ignore"))
    profile["candidate_name"] = resume_path.stem
    return profile


def main() -> None:
    profile = load_latest_candidate_profile()
    sources = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
    matches = run_daily_scan(profile, sources, fetcher=fetch_html, db_path=DEFAULT_DB_PATH)
    print(build_report(matches))


if __name__ == "__main__":
    main()