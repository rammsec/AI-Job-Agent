import json

from src.export_site_data import classify_role_track, deduplicate_matches, export_jobs_payload
from src.job_scanner import compute_job_score, scan_jobs_for_profile


def test_ashby_and_smartrecruiters_sources_normalize_and_filter():
    payloads = {
        "https://api.ashbyhq.com/posting-api/job-board/demoashby?includeCompensation=true": json.dumps({
            "jobs": [
                {
                    "id": "ashby-1",
                    "title": "Applied AI Engineer",
                    "location": "Sydney, Australia",
                    "isRemote": True,
                    "department": "AI",
                    "team": "Applied",
                    "employmentType": "FullTime",
                    "publishedAt": "2026-11-18T00:00:00Z",
                    "jobUrl": "https://jobs.ashbyhq.com/demoashby/ashby-1",
                    "descriptionPlain": "Build Python and LLM workflows.",
                },
                {
                    "id": "ashby-2",
                    "title": "Applied AI Engineer",
                    "location": "San Francisco, CA",
                    "jobUrl": "https://jobs.ashbyhq.com/demoashby/ashby-2",
                    "descriptionPlain": "Python and LLM workflows.",
                },
            ]
        }),
        "https://api.smartrecruiters.com/v1/companies/DemoSR/postings?limit=100&offset=0&country=au": json.dumps({
            "totalFound": 1,
            "content": [
                {
                    "id": "sr-1",
                    "name": "Senior Data Engineer",
                    "location": {"city": "Sydney", "region": "NSW", "country": "au"},
                    "releasedDate": "2026-11-17T00:00:00Z",
                    "department": {"label": "Data"},
                    "typeOfEmployment": {"label": "Full-time"},
                }
            ],
        }),
    }

    profile = {"keywords": ["python", "llm", "data"], "phrases": ["data engineer"]}
    sources = [
        {"name": "Demo Ashby (Ashby)", "source_type": "ashby", "board_name": "demoashby", "target_filter": True},
        {"name": "Demo SR (SmartRecruiters)", "source_type": "smartrecruiters", "company": "DemoSR", "country": "au", "target_filter": True},
    ]

    matches = scan_jobs_for_profile(profile, sources, fetcher=lambda url: payloads[url])

    assert len(matches) == 2
    assert {m["title"] for m in matches} == {"Applied AI Engineer", "Senior Data Engineer"}
    ashby_job = next(m for m in matches if m["source"] == "Demo Ashby (Ashby)")
    assert "Remote" in ashby_job["location"]


def test_phrase_match_boosts_exact_role_score():
    profile = {
        "keywords": ["power", "bi", "data"],
        "phrases": ["power bi"],
    }
    exact = compute_job_score(profile, {"title": "Power BI Analyst", "description": "Build Power BI dashboards"})
    incidental = compute_job_score(profile, {"title": "Power Systems Analyst", "description": "BI tools and data"})
    assert exact["score"] > incidental["score"]


def test_export_jobs_payload_skips_adzuna_without_keys_and_writes_json(tmp_path, monkeypatch):
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)

    out_file = tmp_path / "jobs.json"
    ashby_payload = json.dumps({
        "jobs": [{
            "id": "a1",
            "title": "Power BI Developer",
            "location": "Melbourne, VIC",
            "jobUrl": "https://jobs.ashbyhq.com/x/a1",
            "descriptionPlain": "Power BI, SQL, and Tableau dashboards.",
            "publishedAt": "2026-11-18T00:00:00Z",
        }]
    })

    payload = export_jobs_payload(
        profile={"candidate_name": "Test Profile", "keywords": ["power", "bi", "sql", "tableau"], "phrases": ["power bi"]},
        sources=[
            {"name": "Adzuna AU", "source_type": "adzuna", "enabled": True},
            {"name": "Test Ashby", "source_type": "ashby", "board_name": "x", "enabled": True, "target_filter": True},
        ],
        fetcher=lambda url: ashby_payload,
        output_path=out_file,
        db_path=None,
    )

    assert out_file.exists()
    assert payload["adzuna_enabled"] is False
    assert payload["total_jobs"] == 1
    assert payload["jobs"][0]["track"] == "BI & Analytics"
    assert payload["jobs"][0]["state"] == "VIC"
    assert any("Adzuna" in w for w in payload["source_warnings"])


def test_deduplicate_and_classify_tracks():
    assert classify_role_track({"title": "Machine Learning Engineer"}) == "AI & ML"
    assert classify_role_track({"title": "Analytics Engineer"}) == "Data Engineering"
    assert classify_role_track({"title": "Tableau Developer"}) == "BI & Analytics"

    deduped = deduplicate_matches([
        {"url": "https://example.com/1", "title": "Data Analyst", "score": 20},
        {"url": "https://example.com/1", "title": "Data Analyst", "score": 45},
    ])
    assert len(deduped) == 1
    assert deduped[0]["score"] == 45
