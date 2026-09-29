import base64
import json
import threading
from types import SimpleNamespace
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from src import local_ui


@pytest.fixture
def local_server(monkeypatch, tmp_path):
    input_folder = tmp_path / "resumes"
    source_config = tmp_path / "job_sources.json"
    source_config.write_text(
        json.dumps([{"name": "Adzuna Australia", "source_type": "adzuna", "enabled": True}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(local_ui, "INPUT_FOLDER", input_folder)
    monkeypatch.setattr(local_ui, "SOURCE_CONFIG", source_config)
    monkeypatch.setattr(local_ui, "UI_FILE", local_ui.PROJECT_ROOT / "src" / "static" / "index.html")
    monkeypatch.setattr(local_ui, "DEFAULT_DB_PATH", tmp_path / "career_agent.db")
    monkeypatch.setattr(local_ui, "_latest_resume_profile", lambda: {})

    server = ThreadingHTTPServer(("127.0.0.1", 0), local_ui.LocalUIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()


def post_json(url, payload):
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request) as response:
        return json.loads(response.read())


def test_local_ui_serves_workbench_and_status(local_server):
    with urlopen(local_server) as response:
        page = response.read().decode("utf-8")
    with urlopen(f"{local_server}/api/status") as response:
        status = json.loads(response.read())

    assert "Your job search" in page
    assert status["resume_count"] == 0
    assert status["sources"][0]["name"] == "Adzuna Australia"
    assert status["credentials_ready"] is False


def test_local_ui_accepts_resume_and_rejects_unsupported_file(local_server):
    response = post_json(
        f"{local_server}/api/upload",
        {"filename": "sample.txt", "content": base64.b64encode(b"Python and accounting").decode("ascii")},
    )

    assert response["filename"] == "sample.txt"
    with urlopen(f"{local_server}/api/status") as status_response:
        status = json.loads(status_response.read())
    assert status["resume_count"] == 1
    assert status["latest_resume"] == "sample.txt"

    request = Request(
        f"{local_server}/api/upload",
        data=json.dumps({"filename": "notes.exe", "content": "eA=="}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(HTTPError) as error:
        urlopen(request)
    assert error.value.code == 400


def test_local_ui_scan_returns_and_persists_job_matches(local_server, monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "test-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "test-key")
    monkeypatch.setattr(
        local_ui,
        "_latest_resume_profile",
        lambda: {"candidate_name": "Candidate", "keywords": ["accounts", "payable"], "years_experience": 4},
    )
    monkeypatch.setattr(
        local_ui,
        "fetch_html",
        lambda url: json.dumps({
            "results": [{
                "title": "Accounts Payable Officer",
                "description": "Manage accounts payable processing",
                "redirect_url": "https://example.com/jobs/42",
                "company": {"display_name": "Example Co"},
                "location": {"display_name": "Melbourne VIC"},
            }]
        }),
    )

    result = post_json(f"{local_server}/api/scan", {})
    with urlopen(f"{local_server}/api/status") as response:
        status = json.loads(response.read())

    assert result["matches"][0]["title"] == "Accounts Payable Officer"
    assert result["matches"][0]["url"] == "https://example.com/jobs/42"
    assert status["recent_matches"][0]["source"] == "Adzuna Australia"
    assert status["recent_matches"][0]["url"] == "https://example.com/jobs/42"


def test_local_ui_runs_project_tests_and_returns_summary(local_server, monkeypatch):
    monkeypatch.setattr(
        local_ui.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="17 passed in 2.00s\n", stderr=""),
    )

    result = post_json(f"{local_server}/api/tests", {})

    assert result["passed"] is True
    assert result["summary"] == "17 passed in 2.00s"
    assert "pytest" in local_ui.sys.executable or result["output"] == "17 passed in 2.00s"


def test_local_ui_saves_adzuna_search_settings(local_server):
    result = post_json(
        f"{local_server}/api/search-settings",
        {"what": "", "where": "Sydney NSW"},
    )

    assert result["search_settings"] == {"what": "", "where": "Sydney NSW"}
    with urlopen(f"{local_server}/api/status") as response:
        status = json.loads(response.read())
    assert status["search_settings"] == {"what": "", "where": "Sydney NSW"}