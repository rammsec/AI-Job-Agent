import base64
import binascii
import json
import os
import subprocess
import sys
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.job_filters import is_target_australia_job
from src.job_report import run_daily_scan
from src.job_scanner import attach_profile_scores, build_search_key, fetch_html, score_jobs_for_profile
from src.profile_builder import analyze_job_fit, extract_candidate_profile
from src.resume_reader import INPUT_FOLDER, SUPPORTED_EXTENSIONS, process_resumes
from src.run_job_scan import (
    ACTIVE_PROFILE_PATH,
    RESUME_TEXT_FOLDER,
    list_candidate_profiles,
    load_latest_candidate_profile,
    set_active_candidate_profile,
)
from src.scheduler import (
    AutoScanDaemon,
    DEFAULT_SCHEDULER_CONFIG,
    load_scheduler_state,
    run_scheduled_scan_once,
    update_scheduler_settings,
)
from src.store import (
    DEFAULT_DB_PATH,
    create_database,
    get_job_listing_options,
    get_jobs_for_listing,
    get_stored_jobs,
    has_job_scans,
    save_job_if_changed,
    update_job_status,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_CONFIG = PROJECT_ROOT / "data" / "job_sources.json"
CREDENTIALS_PATH = PROJECT_ROOT / "data" / ".adzuna_credentials.json"
KEY_CREDENTIALS_PATH = PROJECT_ROOT / "key" / "adzunakey.txt"
SCHEDULER_CONFIG_PATH = DEFAULT_SCHEDULER_CONFIG
UI_FILE = PROJECT_ROOT / "src" / "static" / "index.html"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _read_credential_mapping(path: Path) -> dict:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    stripped = raw_text.strip()
    if not stripped:
        return {}

    payload = {}
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = {}

    if isinstance(payload, dict) and (
        payload.get("app_id") or payload.get("ADZUNA_APP_ID") or payload.get("app_key") or payload.get("ADZUNA_APP_KEY")
    ):
        return {
            "app_id": payload.get("app_id") or payload.get("ADZUNA_APP_ID"),
            "app_key": payload.get("app_key") or payload.get("ADZUNA_APP_KEY"),
        }

    mapping = {}
    for line in stripped.splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#"):
            continue
        if "=" in cleaned:
            key, value = cleaned.split("=", 1)
            mapping[key.strip().lower()] = value.strip()
        elif ":" in cleaned:
            key, value = cleaned.split(":", 1)
            mapping[key.strip().lower()] = value.strip()
    if "app_id" in mapping:
        return {"app_id": mapping["app_id"], "app_key": mapping.get("app_key") or mapping.get("api_key")}
    if "adzuna_app_id" in mapping:
        return {"app_id": mapping["adzuna_app_id"], "app_key": mapping.get("adzuna_app_key") or mapping.get("app_key")}
    return {}


def load_saved_adzuna_credentials() -> bool:
    paths = [KEY_CREDENTIALS_PATH, CREDENTIALS_PATH]
    for path in paths:
        credentials = _read_credential_mapping(path)
        if not credentials:
            continue
        for variable, key in (("ADZUNA_APP_ID", "app_id"), ("ADZUNA_APP_KEY", "app_key")):
            value = credentials.get(key)
            if value:
                os.environ[variable] = str(value)
        if os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY"):
            return True
    return bool(os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY"))


def save_adzuna_credentials(app_id: str, app_key: str) -> None:
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = CREDENTIALS_PATH.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps({"app_id": app_id, "app_key": app_key}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(CREDENTIALS_PATH)

    KEY_CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_CREDENTIALS_PATH.write_text(
        f"app_id={app_id}\napp_key={app_key}\n",
        encoding="utf-8",
    )


def _latest_resume_profile() -> dict:
    try:
        return load_latest_candidate_profile(
            text_folder=RESUME_TEXT_FOLDER,
            config_path=ACTIVE_PROFILE_PATH,
        )
    except FileNotFoundError:
        return {}


def execute_configured_scan() -> dict:
    load_saved_adzuna_credentials()
    process_resumes()
    profile = _latest_resume_profile()
    if not profile:
        raise ValueError("Upload and process a resume before scanning.")
    sources = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
    source_errors = []
    matches = run_daily_scan(
        profile,
        sources,
        fetcher=fetch_html,
        db_path=DEFAULT_DB_PATH,
        source_error_callback=source_errors.append,
    )
    return {
        "message": f"Scan complete. {len(matches)} matching jobs found.",
        "matches": matches,
        "source_errors": source_errors,
    }


def get_status() -> dict:
    resume_files = sorted(
        (path for path in INPUT_FOLDER.glob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ) if INPUT_FOLDER.exists() else []
    profile = _latest_resume_profile()
    available_profiles = list_candidate_profiles(RESUME_TEXT_FOLDER)
    try:
        sources = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        sources = []

    try:
        stored_jobs = []
        saved_jobs = []
        if DEFAULT_DB_PATH.exists():
            saved_jobs = get_jobs_for_listing(DEFAULT_DB_PATH, limit=25)
            if profile:
                domain = profile.get("domain")
                for source in sources:
                    if source.get("enabled", True):
                        stored_jobs.extend(
                            get_stored_jobs(
                                DEFAULT_DB_PATH,
                                build_search_key(source, profile),
                                target_only=True,
                                domain=domain,
                            )
                        )
        recent_matches = score_jobs_for_profile(profile, stored_jobs) if profile else []
        saved_jobs = attach_profile_scores(profile, saved_jobs)
        has_scans = has_job_scans(DEFAULT_DB_PATH) if DEFAULT_DB_PATH.exists() else False
    except Exception:
        recent_matches = []
        saved_jobs = []
        stored_jobs = []
        has_scans = False

    return {
        "resume_count": len(resume_files),
        "latest_resume": resume_files[0].name if resume_files else None,
        "profile_ready": bool(profile),
        "profiles": available_profiles,
        "active_profile": profile.get("candidate_name", "") if profile else "",
        "profile": {
            "candidate_name": profile.get("candidate_name", ""),
            "keywords": profile.get("keywords", []),
            "phrases": profile.get("phrases", []),
            "domain": profile.get("domain", "data_ai"),
            "years_experience": profile.get("years_experience", 0),
        } if profile else {},
        "credentials_ready": bool(os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY")),
        "sources": [
            {
                "name": source.get("name", "Unknown source"),
                "source_type": source.get("source_type", "rss"),
                "enabled": source.get("enabled", True),
            }
            for source in sources
        ],
        "search_settings": next(
            (
                {"what": source.get("what", ""), "where": source.get("where", "")}
                for source in sources
                if source.get("source_type") == "adzuna"
            ),
            {"what": "", "where": ""},
        ),
        "scheduler": load_scheduler_state(SCHEDULER_CONFIG_PATH),
        "recent_matches": recent_matches,
        "jobs": saved_jobs,
        "has_scans": has_scans,
        "stored_job_count": len(stored_jobs) if profile else len(saved_jobs),
    }


def save_resume_upload(filename: str, encoded_content: str) -> str:
    safe_name = Path(filename).name
    suffix = Path(safe_name).suffix.lower()
    if not safe_name or suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError("Upload a PDF, DOCX, or TXT resume.")

    try:
        content = base64.b64decode(encoded_content, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("The uploaded file could not be decoded.") from error
    if not content:
        raise ValueError("The uploaded file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("Resume uploads must be 10 MB or smaller.")

    INPUT_FOLDER.mkdir(parents=True, exist_ok=True)
    destination = INPUT_FOLDER / safe_name
    destination.write_bytes(content)
    return safe_name


def save_manual_seek_job(payload: dict) -> dict:
    title = str(payload.get("title", "")).strip()[:240]
    company = str(payload.get("company", "")).strip()[:240]
    location = str(payload.get("location", "")).strip()[:240]
    description = str(payload.get("description", "")).strip()[:12000]
    job_url = str(payload.get("url", "")).strip()[:2048]
    parsed_url = urlparse(job_url)
    hostname = (parsed_url.hostname or "").lower()

    is_seek_australia_host = (
        hostname == "au.seek.com"
        or hostname == "seek.com.au"
        or hostname.endswith(".seek.com.au")
    )
    if parsed_url.scheme != "https" or not is_seek_australia_host:
        raise ValueError("Save a job from the official SEEK Australia site.")
    if not title:
        raise ValueError("The job title is required. Try saving directly from its SEEK result card.")
    if not company:
        raise ValueError("The company name is required.")

    posted_at = str(payload.get("posted_at") or "").strip()[:64] or None
    if posted_at:
        try:
            datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
        except ValueError:
            posted_at = None

    job = {
        "source": "SEEK (manual save)",
        "search_key": "seek-manual-import",
        "external_id": parsed_url.path.rstrip("/") or job_url,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "url": job_url,
        "posted_at": posted_at,
        "salary_min": payload.get("salary_min"),
        "salary_max": payload.get("salary_max"),
        "search_term": str(payload.get("search_term") or "").strip()[:120],
        "raw_json": payload,
    }
    profile = _latest_resume_profile()
    domain = profile.get("domain") if isinstance(profile, dict) else None
    if not payload.get("allow_any_scope") and not is_target_australia_job(job, domain=domain):
        raise ValueError("Only Australian jobs within the supported tech-role scope can be imported.")

    create_database(DEFAULT_DB_PATH).close()
    action = save_job_if_changed(DEFAULT_DB_PATH, job)
    return {"saved": action != "skipped", "action": action, "job": job}


class LocalUIHandler(BaseHTTPRequestHandler):
    server_version = "CareerAgentLocal/1.1"

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > MAX_UPLOAD_BYTES + 1024:
            raise ValueError("Invalid request size.")
        return json.loads(self.rfile.read(content_length))

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/api/status":
            self._send_json(get_status())
            return
        if route == "/api/jobs":
            params = parse_qs(urlparse(self.path).query)
            query = (params.get("query", [""])[0] or "").strip()
            limit = max(1, int(params.get("page_size", params.get("limit", ["20"]))[0] or "20"))
            page = max(1, int(params.get("page", ["1"])[0] or "1"))
            source = (params.get("source", [""])[0] or "").strip()
            region = (params.get("region", [""])[0] or "").strip()
            state = (params.get("state", [""])[0] or "").strip().upper()
            job_type = (params.get("job_type", [""])[0] or "").strip()
            status_filter = (params.get("status", [""])[0] or "").strip()
            sort_by = (params.get("sort_by", [""])[0] or "").strip().lower()
            date_from = (params.get("date_from", [""])[0] or "").strip()
            date_to = (params.get("date_to", [""])[0] or "").strip()
            loaded_from = (params.get("loaded_from", [""])[0] or "").strip()
            loaded_to = (params.get("loaded_to", [""])[0] or "").strip()
            try:
                if date_from:
                    date.fromisoformat(date_from)
                if date_to:
                    date.fromisoformat(date_to)
                if loaded_from:
                    date.fromisoformat(loaded_from)
                if loaded_to:
                    date.fromisoformat(loaded_to)
                if date_from and date_to and date_from > date_to:
                    raise ValueError("Start date must be before or equal to end date.")
                if loaded_from and loaded_to and loaded_from > loaded_to:
                    raise ValueError("Loaded start date must be before or equal to end date.")
            except ValueError:
                self._send_json({"error": "Dates must use YYYY-MM-DD, and the start date cannot be after the end date."}, 400)
                return
            all_jobs = get_jobs_for_listing(
                DEFAULT_DB_PATH,
                search_query=query,
                limit=limit,
                source_filter=source or None,
                region_filter=region or None,
                state_filter=state or None,
                job_type_filter=job_type or None,
                status_filter=status_filter or None,
                date_from=date_from or None,
                date_to=date_to or None,
                loaded_from=loaded_from or None,
                loaded_to=loaded_to or None,
                target_only=False,
                include_all=True,
            )
            profile = _latest_resume_profile()
            all_jobs = attach_profile_scores(profile, all_jobs)
            if sort_by == "score":
                all_jobs.sort(key=lambda item: (item.get("score", 0), item.get("posted_at") or ""), reverse=True)
            elif sort_by == "salary":
                all_jobs.sort(
                    key=lambda item: (
                        float(item.get("salary_max") or item.get("salary_min") or 0),
                        item.get("score", 0),
                    ),
                    reverse=True,
                )

            total = len(all_jobs)
            total_pages = max(1, (total + limit - 1) // limit)
            if page > total_pages:
                page = total_pages
            start_idx = (page - 1) * limit
            paged_jobs = all_jobs[start_idx : start_idx + limit]

            options = get_job_listing_options(DEFAULT_DB_PATH)
            self._send_json({
                "jobs": paged_jobs,
                "query": query,
                "total": total,
                "page": page,
                "page_size": limit,
                "total_pages": total_pages,
                "has_prev": page > 1,
                "has_next": page < total_pages,
                "sources": options["sources"],
                "regions": options["regions"],
                "states": options["states"],
                "job_types": options["job_types"],
                "statuses": options.get("statuses", []),
            })
            return
        if route != "/":
            self._send_json({"error": "Not found."}, 404)
            return

        try:
            content = UI_FILE.read_bytes()
        except OSError:
            self._send_json({"error": "The local UI page is missing."}, 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        try:
            payload = self._read_json()
            if route == "/api/jobs/import":
                result = save_manual_seek_job(payload)
            elif route == "/api/jobs/status":
                updated = update_job_status(
                    DEFAULT_DB_PATH,
                    url=str(payload.get("url", "")),
                    status=str(payload.get("status", "")),
                    notes=payload.get("notes"),
                )
                result = {
                    "updated": updated,
                    "message": f"Marked job as {payload.get('status', '')}.",
                }
            elif route == "/api/jobs/analyze":
                profile = _latest_resume_profile()
                if not profile:
                    raise ValueError("Process a resume first to analyze job fit.")
                analysis = analyze_job_fit(profile, payload)
                result = {"analysis": analysis}
            elif route == "/api/profile/select":
                candidate_name = str(payload.get("candidate_name", "")).strip()
                profile = set_active_candidate_profile(
                    candidate_name,
                    text_folder=RESUME_TEXT_FOLDER,
                    config_path=ACTIVE_PROFILE_PATH,
                )
                result = {
                    "message": f"Switched active profile to {profile.get('candidate_name')}.",
                    "profile": {
                        "candidate_name": profile.get("candidate_name", ""),
                        "keywords": profile.get("keywords", []),
                        "phrases": profile.get("phrases", []),
                        "domain": profile.get("domain", "data_ai"),
                        "years_experience": profile.get("years_experience", 0),
                    },
                }
            elif route == "/api/sources/toggle":
                source_name = str(payload.get("name", "")).strip()
                enabled = bool(payload.get("enabled", True))
                sources = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
                matched = False
                for source in sources:
                    if source.get("name") == source_name:
                        source["enabled"] = enabled
                        matched = True
                if not matched:
                    raise ValueError(f"Source '{source_name}' not found.")
                SOURCE_CONFIG.write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")
                result = {"message": f"{'Enabled' if enabled else 'Disabled'} {source_name}."}
            elif route == "/api/scheduler":
                enabled = bool(payload.get("enabled", False))
                interval_minutes = int(payload.get("interval_minutes", 240))
                scheduler_state = update_scheduler_settings(
                    enabled=enabled,
                    interval_minutes=interval_minutes,
                    config_path=SCHEDULER_CONFIG_PATH,
                )
                if payload.get("run_now"):
                    scheduler_state = run_scheduled_scan_once(
                        execute_configured_scan,
                        config_path=SCHEDULER_CONFIG_PATH,
                    )
                result = {
                    "message": scheduler_state.get("last_message", "Scheduler updated."),
                    "scheduler": scheduler_state,
                }
            elif route == "/api/upload":
                filename = save_resume_upload(payload.get("filename", ""), payload.get("content", ""))
                result = {"message": f"Uploaded {filename}.", "filename": filename}
            elif route == "/api/credentials":
                app_id = str(payload.get("app_id", "")).strip()
                app_key = str(payload.get("app_key", "")).strip()
                if not app_id or not app_key:
                    raise ValueError("Enter both Adzuna API credentials.")
                save_adzuna_credentials(app_id, app_key)
                os.environ["ADZUNA_APP_ID"] = app_id
                os.environ["ADZUNA_APP_KEY"] = app_key
                result = {"message": "Adzuna credentials saved locally for future sessions."}
            elif route == "/api/search-settings":
                what = str(payload.get("what", "")).strip()[:120]
                where = str(payload.get("where", "")).strip()[:120]
                sources = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
                adzuna_sources = [source for source in sources if source.get("source_type") == "adzuna"]
                if not adzuna_sources:
                    raise ValueError("No Adzuna source is configured.")
                adzuna_sources[0]["what"] = what
                for adzuna_source in adzuna_sources:
                    adzuna_source["where"] = where
                SOURCE_CONFIG.write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")
                result = {
                    "message": "Search settings saved.",
                    "search_settings": {"what": what, "where": where},
                }
            elif route == "/api/ingest":
                process_resumes()
                profile = _latest_resume_profile()
                if not profile:
                    profile = load_latest_candidate_profile()
                result = {
                    "message": "Resume ingestion completed.",
                    "profile": {
                        "candidate_name": profile.get("candidate_name", ""),
                        "keywords": profile.get("keywords", []),
                        "phrases": profile.get("phrases", []),
                        "domain": profile.get("domain", "data_ai"),
                        "years_experience": profile.get("years_experience", 0),
                    },
                }
            elif route == "/api/scan":
                profile = _latest_resume_profile()
                if not profile:
                    raise ValueError("Upload and process a resume before scanning.")
                sources = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
                source_errors = []
                matches = run_daily_scan(
                    profile,
                    sources,
                    fetcher=fetch_html,
                    db_path=DEFAULT_DB_PATH,
                    source_error_callback=source_errors.append,
                )
                result = {
                    "message": f"Scan complete. {len(matches)} matching jobs found.",
                    "matches": matches,
                    "source_errors": source_errors,
                }
            elif route == "/api/tests":
                try:
                    completed = subprocess.run(
                        [sys.executable, "-m", "pytest", "-q"],
                        cwd=PROJECT_ROOT,
                        capture_output=True,
                        text=True,
                        timeout=180,
                        check=False,
                    )
                    output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
                    summary = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), "No test output.")
                    result = {
                        "passed": completed.returncode == 0,
                        "summary": summary,
                        "output": output[-8000:],
                    }
                except subprocess.TimeoutExpired as error:
                    output = error.stdout or ""
                    if isinstance(output, bytes):
                        output = output.decode("utf-8", errors="replace")
                    result = {
                        "passed": False,
                        "summary": "Test run timed out after 180 seconds.",
                        "output": output[-8000:],
                    }
            else:
                self._send_json({"error": "Not found."}, 404)
                return
            self._send_json(result)
        except (ValueError, json.JSONDecodeError, FileNotFoundError) as error:
            self._send_json({"error": str(error)}, 400)
        except Exception as error:
            message = str(error) or "The operation failed."
            status = 400 if "HTTP 400" in message or "Bad Request" in message or "credentials" in message.lower() else 500
            self._send_json({"error": message}, status)

    def log_message(self, format: str, *args) -> None:
        print(f"[local-ui] {self.address_string()} - {format % args}")


def main() -> None:
    load_saved_adzuna_credentials()
    daemon = AutoScanDaemon(execute_configured_scan, config_path=SCHEDULER_CONFIG_PATH)
    daemon.start()
    server = ThreadingHTTPServer(("127.0.0.1", 8765), LocalUIHandler)
    print("AI Career Agent UI is running at http://127.0.0.1:8765")
    print("Press Ctrl+C to stop the local server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AI Career Agent UI.")
    finally:
        daemon.stop()
        server.server_close()


if __name__ == "__main__":
    main()
