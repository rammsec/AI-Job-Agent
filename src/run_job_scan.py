import json
import os
from pathlib import Path

from src.job_report import build_report, run_daily_scan
from src.job_scanner import fetch_html
from src.profile_builder import extract_candidate_profile
from src.resume_reader import process_resumes
from src.store import DEFAULT_DB_PATH


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESUME_TEXT_FOLDER = PROJECT_ROOT / "data" / "output" / "text"
SOURCE_CONFIG = PROJECT_ROOT / "data" / "job_sources.json"
ACTIVE_PROFILE_PATH = PROJECT_ROOT / "data" / "active_profile.json"
CREDENTIALS_PATH = PROJECT_ROOT / "data" / ".adzuna_credentials.json"
KEY_CREDENTIALS_PATH = PROJECT_ROOT / "key" / "adzunakey.txt"


def list_candidate_profiles(text_folder: Path = RESUME_TEXT_FOLDER) -> list[dict]:
    if not text_folder.exists():
        return []
    resume_text_files = sorted(
        text_folder.glob("*.txt"),
        key=lambda file_path: file_path.stat().st_mtime,
        reverse=True,
    )
    profiles = []
    for path in resume_text_files:
        profile = extract_candidate_profile(path.read_text(encoding="utf-8", errors="ignore"))
        profiles.append({
            "candidate_name": path.stem,
            "domain": profile.get("domain", "data_ai"),
            "years_experience": profile.get("years_experience", 0),
            "keyword_count": len(profile.get("keywords", [])),
        })
    return profiles


def get_active_candidate_name(config_path: Path = ACTIVE_PROFILE_PATH) -> str | None:
    if not config_path.exists():
        return None
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        name = str(payload.get("candidate_name") or "").strip()
        return name or None
    except (OSError, json.JSONDecodeError):
        return None


def set_active_candidate_profile(
    candidate_name: str,
    text_folder: Path = RESUME_TEXT_FOLDER,
    config_path: Path = ACTIVE_PROFILE_PATH,
) -> dict:
    clean_name = str(candidate_name or "").strip()
    if not clean_name:
        raise ValueError("Select a valid candidate profile.")
    target_path = text_folder / f"{clean_name}.txt"
    if not target_path.exists():
        raise FileNotFoundError(f"Resume profile '{clean_name}' was not found.")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"candidate_name": clean_name}, indent=2) + "\n",
        encoding="utf-8",
    )
    return load_latest_candidate_profile(
        text_folder=text_folder,
        candidate_name=clean_name,
        config_path=config_path,
    )


def load_latest_candidate_profile(
    text_folder: Path = RESUME_TEXT_FOLDER,
    candidate_name: str | None = None,
    config_path: Path = ACTIVE_PROFILE_PATH,
) -> dict:
    resume_text_files = sorted(
        text_folder.glob("*.txt"),
        key=lambda file_path: file_path.stat().st_mtime,
        reverse=True,
    ) if text_folder.exists() else []
    if not resume_text_files:
        raise FileNotFoundError(
            f"No extracted resume text found in {text_folder}. Run the resume ingestion first."
        )

    preferred_name = candidate_name or get_active_candidate_name(config_path)
    resume_path = None
    if preferred_name:
        candidate_file = text_folder / f"{preferred_name}.txt"
        if candidate_file.exists():
            resume_path = candidate_file

    if resume_path is None:
        resume_path = resume_text_files[0]

    profile = extract_candidate_profile(resume_path.read_text(encoding="utf-8", errors="ignore"))
    profile["candidate_name"] = resume_path.stem
    return profile


def _load_cli_credentials() -> None:
    if os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY"):
        return
    for path in (KEY_CREDENTIALS_PATH, CREDENTIALS_PATH):
        if not path.exists():
            continue
        try:
            raw = path.read_text(encoding="utf-8").strip()
            if raw.startswith("{"):
                data = json.loads(raw)
                app_id = data.get("app_id") or data.get("ADZUNA_APP_ID")
                app_key = data.get("app_key") or data.get("ADZUNA_APP_KEY")
            else:
                mapping = {}
                for line in raw.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        mapping[k.strip().lower()] = v.strip()
                app_id = mapping.get("app_id") or mapping.get("adzuna_app_id")
                app_key = mapping.get("app_key") or mapping.get("adzuna_app_key")
            if app_id and app_key:
                os.environ["ADZUNA_APP_ID"] = str(app_id)
                os.environ["ADZUNA_APP_KEY"] = str(app_key)
                return
        except (OSError, json.JSONDecodeError):
            continue


def main() -> None:
    _load_cli_credentials()
    process_resumes()
    profile = load_latest_candidate_profile()
    sources = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
    source_errors = []
    matches = run_daily_scan(
        profile,
        sources,
        fetcher=fetch_html,
        db_path=DEFAULT_DB_PATH,
        source_error_callback=source_errors.append,
    )
    print(build_report(matches))
    if source_errors:
        print(f"\nNote: {len(source_errors)} source(s) reported warnings during scan.")


if __name__ == "__main__":
    main()
