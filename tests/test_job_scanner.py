from src.job_scanner import scan_jobs_for_profile


PROFILE = {
    "keywords": ["python", "fastapi", "azure", "sql"],
    "years_experience": 5,
}


SOURCES = [
    {
        "name": "demo-source",
        "search_url": "https://example.com/search",
        "html": """
        <html>
          <body>
            <h2>Senior Python Developer</h2>
            <p>Need FastAPI, Azure, SQL and Python experience.</p>
            <a href="https://example.com/job/1">Apply</a>
            <h3>Data Analyst</h3>
            <p>Need Excel, reporting, Power BI</p>
          </body>
        </html>
        """,
    }
]


def test_scan_jobs_for_profile_uses_candidate_keywords_and_ranks_matches():
    jobs = scan_jobs_for_profile(PROFILE, sources=SOURCES, fetcher=lambda url: SOURCES[0]["html"])

    assert jobs[0]["title"] == "Senior Python Developer"
    assert len(jobs) == 1
    assert "python" in jobs[0]["matches"]


def test_title_skill_overlap_ranks_above_description_only_overlap():
    profile = {"keywords": ["sql", "python"], "years_experience": 20}
    jobs = [
        {
            "name": "demo-source",
            "search_url": "https://example.com/search",
            "html": """
                <html><body>
                    <h2>Operations Coordinator</h2>
                    <p>Experience with SQL and Python preferred.</p>
                    <h2>Python Developer</h2>
                    <p>Build reliable applications.</p>
                </body></html>
        """,
        }
    ]

    results = scan_jobs_for_profile(profile, jobs, fetcher=lambda url: jobs[0]["html"])

    assert results[0]["title"] == "Python Developer"
    assert results[0]["title_matches"] == ["python"]
    assert results[0]["score"] > results[1]["score"]


def test_keyword_is_not_double_counted_when_present_in_title_and_description():
    profile = {"keywords": ["data"], "years_experience": 0}
    jobs = [{
        "name": "demo-source",
        "search_url": "https://example.com/search",
        "html": "<html><body><h2>Data Analyst</h2><p>Analyse data every day.</p></body></html>",
    }]

    results = scan_jobs_for_profile(profile, jobs, fetcher=lambda url: jobs[0]["html"])

    assert results[0]["score"] == 15
    assert results[0]["matches"] == ["data"]


def test_seniority_adjustment_prefers_senior_role_to_internship():
    profile = {"keywords": ["data"], "years_experience": 11}
    jobs = [{
        "name": "demo-source",
        "search_url": "https://example.com/search",
        "html": """
            <html><body>
                <h2>Data Internship</h2><p>Work with data tools.</p>
                <h2>Senior Data Analyst</h2><p>Work with data tools.</p>
            </body></html>
        """,
    }]

    results = scan_jobs_for_profile(profile, jobs, fetcher=lambda url: jobs[0]["html"])

    assert results[0]["title"] == "Senior Data Analyst"
    assert results[0]["seniority_adjustment"] == 5
    assert results[1]["seniority_adjustment"] == -10
