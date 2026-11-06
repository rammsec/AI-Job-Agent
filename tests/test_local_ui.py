import base64
import json
import threading
from types import SimpleNamespace
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from src import local_ui
from src.job_scanner import build_search_key
from src.store import create_database, save_fetched_jobs


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
    monkeypatch.setattr(local_ui, "CREDENTIALS_PATH", tmp_path / ".adzuna_credentials.json")
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
        lambda: {"candidate_name": "Candidate", "keywords": ["power", "bi"], "years_experience": 4},
    )
    monkeypatch.setattr(
        local_ui,
        "fetch_html",
        lambda url: json.dumps({
            "results": [{
                    "title": "Power BI Analyst",
                    "description": "Create Power BI reports for finance",
                "redirect_url": "https://example.com/jobs/42",
                "company": {"display_name": "Example Co"},
                "location": {"display_name": "Melbourne VIC"},
            }]
        }),
    )

    result = post_json(f"{local_server}/api/scan", {})
    with urlopen(f"{local_server}/api/status") as response:
        status = json.loads(response.read())

    assert result["matches"][0]["title"] == "Power BI Analyst"
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


def test_local_ui_surfaces_clear_error_when_adzuna_rejects_scan(local_server, monkeypatch):
    monkeypatch.setattr(local_ui, "_latest_resume_profile", lambda: {"candidate_name": "Candidate", "keywords": ["sql"], "years_experience": 3})
    monkeypatch.setattr(
        local_ui,
        "run_daily_scan",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Adzuna API rejected the request with HTTP 400. Check your saved credentials and source filters.")),
    )

    request = Request(
        f"{local_server}/api/scan",
        data=b"{}",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(HTTPError) as error:
        urlopen(request)

    assert error.value.code == 400
    payload = json.loads(error.value.read().decode("utf-8"))
    assert "Adzuna" in payload["error"]
    assert "HTTP 400" in payload["error"]


def test_local_ui_saves_adzuna_search_settings(local_server):
    result = post_json(
        f"{local_server}/api/search-settings",
        {"what": "", "where": "Sydney NSW"},
    )

    assert result["search_settings"] == {"what": "", "where": "Sydney NSW"}
    with urlopen(f"{local_server}/api/status") as response:
        status = json.loads(response.read())
    assert status["search_settings"] == {"what": "", "where": "Sydney NSW"}


def test_local_ui_saves_and_reloads_adzuna_credentials(local_server, monkeypatch):
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)

    result = post_json(
        f"{local_server}/api/credentials",
        {"app_id": "test-app-id", "app_key": "test-app-key"},
    )
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)

    assert result["message"] == "Adzuna credentials saved locally for future sessions."
    assert local_ui.load_saved_adzuna_credentials() is True
    assert local_ui.os.environ["ADZUNA_APP_ID"] == "test-app-id"
    assert local_ui.os.environ["ADZUNA_APP_KEY"] == "test-app-key"


def test_local_ui_loads_adzuna_credentials_from_key_file(local_server, monkeypatch, tmp_path):
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    monkeypatch.setattr(local_ui, "CREDENTIALS_PATH", tmp_path / ".adzuna_credentials.json")
    key_path = tmp_path / "key" / "adzunakey.txt"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text("app_id=txt-app-id\napp_key=txt-app-key\n", encoding="utf-8")
    monkeypatch.setattr(local_ui, "KEY_CREDENTIALS_PATH", key_path)

    assert local_ui.load_saved_adzuna_credentials() is True
    assert local_ui.os.environ["ADZUNA_APP_ID"] == "txt-app-id"
    assert local_ui.os.environ["ADZUNA_APP_KEY"] == "txt-app-key"


def test_local_ui_prefers_key_file_over_legacy_json_file(local_server, monkeypatch, tmp_path):
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    monkeypatch.setattr(local_ui, "CREDENTIALS_PATH", tmp_path / ".adzuna_credentials.json")
    monkeypatch.setattr(local_ui, "CREDENTIALS_PATH", tmp_path / ".adzuna_credentials.json")
    monkeypatch.setattr(local_ui, "KEY_CREDENTIALS_PATH", tmp_path / "key" / "adzunakey.txt")
    local_ui.CREDENTIALS_PATH.write_text('{"app_id": "json-id", "app_key": "json-key"}', encoding='utf-8')
    local_ui.KEY_CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    local_ui.KEY_CREDENTIALS_PATH.write_text('app_id=txt-app-id\napp_key=txt-app-key\n', encoding='utf-8')

    assert local_ui.load_saved_adzuna_credentials() is True
    assert local_ui.os.environ["ADZUNA_APP_ID"] == "txt-app-id"
    assert local_ui.os.environ["ADZUNA_APP_KEY"] == "txt-app-key"


def test_status_builds_results_from_jobs_table_without_match_history(local_server, monkeypatch):
    profile = {
        "candidate_name": "Candidate",
        "keywords": ["python", "sql"],
        "years_experience": 6,
    }
    monkeypatch.setattr(local_ui, "_latest_resume_profile", lambda: profile)
    with local_ui.SOURCE_CONFIG.open(encoding="utf-8") as config_file:
        source = json.load(config_file)[0]
    create_database(local_ui.DEFAULT_DB_PATH).close()
    save_fetched_jobs(local_ui.DEFAULT_DB_PATH, [{
        "source": "Adzuna Australia",
        "search_key": build_search_key(source, profile),
        "external_id": "stored-job-1",
        "title": "Senior Data Engineer",
        "description": "Build SQL-backed services",
        "url": "https://example.com/jobs/stored-job-1",
        "company": "Example Systems",
        "location": "Melbourne VIC",
        "posted_at": "2026-09-29T08:00:00Z",
        "salary_min": 120000,
    }])

    with urlopen(f"{local_server}/api/status") as response:
        status = json.loads(response.read())

    assert len(status["recent_matches"]) == 1
    stored_match = status["recent_matches"][0]
    assert stored_match["title"] == "Senior Data Engineer"
    assert stored_match["company"] == "Example Systems"
    assert stored_match["location"] == "Melbourne VIC"
    assert status["stored_job_count"] == 1
    assert stored_match["salary_min"] == 120000


def test_local_ui_filters_and_lists_saved_jobs_for_search(local_server):
    create_database(local_ui.DEFAULT_DB_PATH).close()
    save_fetched_jobs(local_ui.DEFAULT_DB_PATH, [
        {
            "source": "Adzuna Australia",
            "search_key": "search-alpha",
            "external_id": "job-1",
            "title": "Senior Power BI Engineer",
            "description": "Design and manage Power BI reports",
            "url": "https://example.com/jobs/job-1",
            "company": "Alpha Data",
            "location": "Melbourne VIC",
            "posted_at": "2026-09-30T12:00:00Z",
        },
        {
            "source": "Adzuna Australia",
            "search_key": "search-beta",
            "external_id": "job-2",
            "title": "Software Developer",
            "description": "Build APIs and automation tools",
            "url": "https://example.com/jobs/job-2",
            "company": "Beta Labs",
            "location": "Sydney NSW",
            "posted_at": "2026-09-29T10:00:00Z",
        },
    ])

    with urlopen(f"{local_server}/api/jobs?query=power%20bi") as response:
        payload = json.loads(response.read())

    assert payload["total"] == 1
    assert payload["jobs"][0]["title"] == "Senior Power BI Engineer"
    assert payload["jobs"][0]["company"] == "Alpha Data"


def test_local_ui_filters_saved_jobs_by_source_region_and_posted_date(local_server):
    create_database(local_ui.DEFAULT_DB_PATH).close()
    save_fetched_jobs(local_ui.DEFAULT_DB_PATH, [
        {
            "source": "Greenhouse Board",
            "search_key": "filter-test",
            "external_id": "job-sydney-new",
            "title": "Power BI Engineer",
            "description": "Build reporting dashboards",
            "url": "https://example.com/jobs/sydney-new",
            "location": "Sydney NSW",
            "posted_at": "2026-10-02T09:00:00Z",
        },
        {
            "source": "Greenhouse Board",
            "search_key": "filter-test",
            "external_id": "job-sydney-old",
            "title": "Data Analyst",
            "description": "Analyse business data",
            "url": "https://example.com/jobs/sydney-old",
            "location": "Sydney NSW",
            "posted_at": "2026-09-29T09:00:00Z",
        },
        {
            "source": "Lever Board",
            "search_key": "filter-test",
            "external_id": "job-melbourne-new",
            "title": "Data Scientist",
            "description": "Build machine learning models",
            "url": "https://example.com/jobs/melbourne-new",
            "location": "Melbourne VIC",
            "posted_at": "2026-10-02T12:00:00Z",
        },
    ])

    with urlopen(
        f"{local_server}/api/jobs?source=Greenhouse+Board&region=Sydney&date_from=2026-10-01&date_to=2026-10-03"
    ) as response:
        payload = json.loads(response.read())

    assert payload["total"] == 1
    assert payload["jobs"][0]["title"] == "Power BI Engineer"
    assert "Greenhouse Board" in payload["sources"]
    assert "Sydney NSW" in payload["regions"]


def test_local_ui_filters_saved_jobs_by_loaded_date(local_server):
    create_database(local_ui.DEFAULT_DB_PATH).close()
    save_fetched_jobs(local_ui.DEFAULT_DB_PATH, [{
        "source": "Test source", "search_key": "loaded-filter", "external_id": "loaded-job",
        "title": "Recently loaded job", "first_seen_at": "2026-10-20T09:00:00+00:00",
    }])

    with urlopen(f"{local_server}/api/jobs?loaded_from=2026-10-20&loaded_to=2026-10-20") as response:
        payload = json.loads(response.read())

    assert payload["total"] == 1
    assert payload["jobs"][0]["first_seen_at"] == "2026-10-20T09:00:00+00:00"


def test_local_ui_filters_saved_jobs_by_job_type_and_returns_filter_options(local_server):
    create_database(local_ui.DEFAULT_DB_PATH).close()
    save_fetched_jobs(local_ui.DEFAULT_DB_PATH, [{
        "source": "Test source", "search_key": "type-filter", "external_id": "contract-job",
        "title": "Contract data role", "contract_type": "contract",
    }])

    with urlopen(f"{local_server}/api/jobs?job_type=Contract") as response:
        payload = json.loads(response.read())

    assert payload["total"] == 1
    assert payload["jobs"][0]["job_type"] == "Contract"
    assert payload["job_types"] == ["Contract"]


def test_local_ui_rejects_invalid_date_filter(local_server):
    create_database(local_ui.DEFAULT_DB_PATH).close()

    with pytest.raises(HTTPError) as error:
        urlopen(f"{local_server}/api/jobs?date_from=not-a-date")

    assert error.value.code == 400
    payload = json.loads(error.value.read().decode("utf-8"))
    assert "YYYY-MM-DD" in payload["error"]


def test_local_ui_imports_user_selected_seek_job(local_server):
    import sqlite3

    result = post_json(f"{local_server}/api/jobs/import", {
        "title": "Data Analyst",
        "company": "Example Australia",
        "location": "Sydney NSW",
        "description": "Analyse business data and build Power BI reports.",
        "url": "https://www.seek.com.au/job/90123456",
        "posted_at": "2026-10-05T10:00:00+11:00",
    })

    assert result["action"] == "inserted"
    assert result["job"]["source"] == "SEEK (manual save)"
    with urlopen(f"{local_server}/api/jobs?source=SEEK+%28manual+save%29") as response:
        listing = json.loads(response.read())
    assert listing["total"] == 1
    assert listing["jobs"][0]["title"] == "Data Analyst"
    with sqlite3.connect(local_ui.DEFAULT_DB_PATH) as connection:
        first_seen, last_seen = connection.execute(
            "SELECT first_seen_at, last_seen_at FROM jobs WHERE source = 'SEEK (manual save)'"
        ).fetchone()

    unchanged = post_json(f"{local_server}/api/jobs/import", {
        "title": "Data Analyst",
        "company": "Example Australia",
        "location": "Sydney NSW",
        "description": "Analyse business data and build Power BI reports.",
        "url": "https://www.seek.com.au/job/90123456",
        "posted_at": "2026-10-05T10:00:00+11:00",
    })
    assert unchanged["action"] == "skipped"
    with sqlite3.connect(local_ui.DEFAULT_DB_PATH) as connection:
        assert connection.execute(
            "SELECT first_seen_at, last_seen_at FROM jobs WHERE source = 'SEEK (manual save)'"
        ).fetchone() == (first_seen, last_seen)

    updated = post_json(f"{local_server}/api/jobs/import", {
        "title": "Senior Data Analyst",
        "company": "Example Australia",
        "location": "Sydney NSW",
        "description": "Analyse business data and build Power BI reports.",
        "url": "https://www.seek.com.au/job/90123456",
    })
    assert updated["action"] == "updated"
    with urlopen(f"{local_server}/api/jobs?source=SEEK+%28manual+save%29") as response:
        listing = json.loads(response.read())
    assert listing["total"] == 1
    assert listing["jobs"][0]["title"] == "Senior Data Analyst"


def test_local_ui_rejects_out_of_scope_seek_job(local_server):
    request = Request(
        f"{local_server}/api/jobs/import",
        data=json.dumps({
            "title": "Sales Manager",
            "company": "Example Australia",
            "location": "Sydney NSW",
            "description": "Manage a sales team.",
            "url": "https://www.seek.com.au/job/90123457",
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    with pytest.raises(HTTPError) as error:
        urlopen(request)

    assert error.value.code == 400
    assert "supported tech-role scope" in error.value.read().decode("utf-8")


def test_local_ui_rejects_non_seek_job_url(local_server):
    request = Request(
        f"{local_server}/api/jobs/import",
        data=json.dumps({
            "title": "Data Analyst",
            "company": "Example Australia",
            "location": "Sydney NSW",
            "url": "https://example.com/jobs/123",
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    with pytest.raises(HTTPError) as error:
        urlopen(request)

    assert error.value.code == 400
    assert "official SEEK Australia site" in error.value.read().decode("utf-8")


def test_local_ui_accepts_au_seek_com_job_url(local_server):
    result = post_json(f"{local_server}/api/jobs/import", {
        "title": "Data Analyst",
        "company": "Example Australia",
        "location": "Sydney NSW",
        "description": "Analyse business data and build Power BI reports.",
        "url": "https://au.seek.com/job/90123458",
    })

    assert result["saved"] is True
    assert result["job"]["url"] == "https://au.seek.com/job/90123458"


def test_local_ui_rejects_seek_import_without_company(local_server):
    request = Request(
        f"{local_server}/api/jobs/import",
        data=json.dumps({
            "title": "Power BI Developer",
            "location": "Sydney NSW",
            "description": "Build reporting dashboards.",
            "url": "https://au.seek.com/job/90123459",
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    with pytest.raises(HTTPError) as error:
        urlopen(request)

    assert error.value.code == 400
    assert "company" in error.value.read().decode("utf-8").lower()



def test_local_ui_updates_and_filters_job_application_status(local_server):
    create_database(local_ui.DEFAULT_DB_PATH).close()
    save_fetched_jobs(local_ui.DEFAULT_DB_PATH, [
        {
            "source": "Adzuna Australia",
            "search_key": "status-test",
            "external_id": "job-status-1",
            "title": "Tableau Developer",
            "description": "Build Tableau dashboards",
            "url": "https://example.com/jobs/status-1",
            "company": "Alpha BI",
            "location": "Melbourne VIC",
        },
        {
            "source": "Adzuna Australia",
            "search_key": "status-test",
            "external_id": "job-status-2",
            "title": "Data Engineer",
            "description": "Build pipelines",
            "url": "https://example.com/jobs/status-2",
            "company": "Beta Data",
            "location": "Sydney NSW",
        },
    ])

    update_result = post_json(f"{local_server}/api/jobs/status", {
        "url": "https://example.com/jobs/status-1",
        "status": "shortlisted",
        "notes": "Great fit for Tableau experience",
    })
    assert update_result["updated"] is True

    with urlopen(f"{local_server}/api/jobs?status=shortlisted") as response:
        payload = json.loads(response.read())

    assert payload["total"] == 1
    assert payload["jobs"][0]["title"] == "Tableau Developer"
    assert payload["jobs"][0]["application_status"] == "shortlisted"
    assert payload["jobs"][0]["notes"] == "Great fit for Tableau experience"


def test_local_ui_attaches_scores_to_jobs_and_supports_score_sorting(local_server, monkeypatch):
    monkeypatch.setattr(
        local_ui,
        "_latest_resume_profile",
        lambda: {"candidate_name": "Candidate", "keywords": ["tableau", "sql"], "years_experience": 6},
    )
    create_database(local_ui.DEFAULT_DB_PATH).close()
    save_fetched_jobs(local_ui.DEFAULT_DB_PATH, [
        {
            "source": "Adzuna Australia",
            "search_key": "sort-test",
            "external_id": "job-low",
            "title": "Operations Coordinator",
            "description": "General office duties",
            "url": "https://example.com/jobs/low",
            "posted_at": "2026-10-05T10:00:00Z",
        },
        {
            "source": "Adzuna Australia",
            "search_key": "sort-test",
            "external_id": "job-high",
            "title": "Senior Tableau & SQL Developer",
            "description": "Build Tableau and SQL analytics",
            "url": "https://example.com/jobs/high",
            "posted_at": "2026-10-01T10:00:00Z",
        },
    ])

    with urlopen(f"{local_server}/api/jobs?sort_by=score") as response:
        payload = json.loads(response.read())

    assert payload["jobs"][0]["title"] == "Senior Tableau & SQL Developer"
    assert payload["jobs"][0]["score"] > payload["jobs"][1]["score"]
    assert "tableau" in payload["jobs"][0]["matches"]


def test_local_ui_configures_and_runs_auto_fetch_scheduler(local_server, monkeypatch, tmp_path):
    monkeypatch.setattr(local_ui, "SCHEDULER_CONFIG_PATH", tmp_path / "scheduler_config.json")
    monkeypatch.setattr(
        local_ui,
        "execute_configured_scan",
        lambda: {"message": "Scan complete.", "matches": [{"title": "Data Engineer", "score": 30}], "source_errors": []},
    )

    result = post_json(f"{local_server}/api/scheduler", {
        "enabled": True,
        "interval_minutes": 60,
        "run_now": True,
    })

    assert result["scheduler"]["enabled"] is True
    assert result["scheduler"]["interval_minutes"] == 60
    assert result["scheduler"]["last_status"] == "success"
    assert result["scheduler"]["last_match_count"] == 1



def test_local_ui_paginates_job_listings(local_server):
    create_database(local_ui.DEFAULT_DB_PATH).close()
    save_fetched_jobs(local_ui.DEFAULT_DB_PATH, [
        {
            "source": "Adzuna Australia",
            "search_key": "page-test",
            "external_id": f"job-{idx}",
            "title": f"Data Engineer {idx}",
            "description": "Build data pipelines",
            "url": f"https://example.com/jobs/page-{idx}",
            "posted_at": f"2026-10-0{idx}T10:00:00Z",
        }
        for idx in range(1, 6)
    ])

    with urlopen(f"{local_server}/api/jobs?page=1&page_size=2") as response:
        page1 = json.loads(response.read())
    with urlopen(f"{local_server}/api/jobs?page=2&page_size=2") as response:
        page2 = json.loads(response.read())
    with urlopen(f"{local_server}/api/jobs?page=3&page_size=2") as response:
        page3 = json.loads(response.read())

    assert page1["total"] == 5
    assert page1["total_pages"] == 3
    assert page1["page"] == 1
    assert len(page1["jobs"]) == 2
    assert page1["has_prev"] is False
    assert page1["has_next"] is True

    assert page2["page"] == 2
    assert len(page2["jobs"]) == 2
    assert page2["has_prev"] is True
    assert page2["has_next"] is True

    assert page3["page"] == 3
    assert len(page3["jobs"]) == 1
    assert page3["has_prev"] is True
    assert page3["has_next"] is False
