import base64
import binascii
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from src.job_report import run_daily_scan
from src.job_scanner import fetch_html
from src.profile_builder import extract_candidate_profile
from src.resume_reader import INPUT_FOLDER, SUPPORTED_EXTENSIONS, process_resumes
from src.run_job_scan import load_latest_candidate_profile
from src.store import DEFAULT_DB_PATH, get_recent_matches


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_CONFIG = PROJECT_ROOT / "data" / "job_sources.json"
UI_FILE = PROJECT_ROOT / "src" / "static" / "index.html"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _latest_resume_profile() -> dict:
    try:
        return load_latest_candidate_profile()
    except FileNotFoundError:
        return {}


def get_status() -> dict:
    resume_files = sorted(
        (path for path in INPUT_FOLDER.glob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    profile = _latest_resume_profile()
    try:
        sources = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        sources = []

    try:
        recent_matches = get_recent_matches(DEFAULT_DB_PATH) if DEFAULT_DB_PATH.exists() else []
    except Exception:
        recent_matches = []

    return {
        "resume_count": len(resume_files),
        "latest_resume": resume_files[0].name if resume_files else None,
        "profile_ready": bool(profile),
        "profile": {
            "candidate_name": profile.get("candidate_name", ""),
            "keywords": profile.get("keywords", []),
            "years_experience": profile.get("years_experience", 0),
        } if profile else {},
        "credentials_ready": bool(os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY")),
        "sources": [
            {"name": source.get("name", "Unknown source"), "enabled": source.get("enabled", True)}
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
        "recent_matches": recent_matches,
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


class LocalUIHandler(BaseHTTPRequestHandler):
    server_version = "CareerAgentLocal/1.0"

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
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
            if route == "/api/upload":
                filename = save_resume_upload(payload.get("filename", ""), payload.get("content", ""))
                result = {"message": f"Uploaded {filename}.", "filename": filename}
            elif route == "/api/credentials":
                app_id = str(payload.get("app_id", "")).strip()
                app_key = str(payload.get("app_key", "")).strip()
                if not app_id or not app_key:
                    raise ValueError("Enter both Adzuna API credentials.")
                os.environ["ADZUNA_APP_ID"] = app_id
                os.environ["ADZUNA_APP_KEY"] = app_key
                result = {"message": "Adzuna credentials are set for this UI session."}
            elif route == "/api/search-settings":
                what = str(payload.get("what", "")).strip()[:120]
                where = str(payload.get("where", "")).strip()[:120]
                sources = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
                adzuna_source = next(
                    (source for source in sources if source.get("source_type") == "adzuna"),
                    None,
                )
                if adzuna_source is None:
                    raise ValueError("No Adzuna source is configured.")
                adzuna_source["what"] = what
                adzuna_source["where"] = where
                SOURCE_CONFIG.write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")
                result = {
                    "message": "Search settings saved.",
                    "search_settings": {"what": what, "where": where},
                }
            elif route == "/api/ingest":
                process_resumes()
                profile = load_latest_candidate_profile()
                result = {
                    "message": "Resume ingestion completed.",
                    "profile": {
                        "candidate_name": profile.get("candidate_name", ""),
                        "keywords": profile.get("keywords", []),
                        "years_experience": profile.get("years_experience", 0),
                    },
                }
            elif route == "/api/scan":
                profile = _latest_resume_profile()
                if not profile:
                    raise ValueError("Upload and process a resume before scanning.")
                sources = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
                matches = run_daily_scan(profile, sources, fetcher=fetch_html, db_path=DEFAULT_DB_PATH)
                result = {"message": f"Scan complete. {len(matches)} jobs found.", "matches": matches}
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
            self._send_json({"error": f"The operation failed: {error}"}, 500)

    def log_message(self, format: str, *args) -> None:
        print(f"[local-ui] {self.address_string()} - {format % args}")


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8765), LocalUIHandler)
    print("AI Career Agent UI is running at http://127.0.0.1:8765")
    print("Press Ctrl+C to stop the local server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping AI Career Agent UI.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()