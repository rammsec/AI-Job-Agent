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
    assert jobs[0]["score"] > jobs[1]["score"]
    assert "python" in jobs[0]["matches"]
