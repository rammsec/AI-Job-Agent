from src.profile_builder import analyze_job_fit, extract_candidate_profile, match_jobs_to_candidate


RESUME_TEXT = """
Senior Python Developer with 5+ years of experience building scalable APIs using FastAPI,
PostgreSQL, SQL, Docker, and Azure. Worked on NLP and machine learning projects,
created REST APIs, and led backend engineering for data products.
"""

JOBS = [
    {
        "title": "Senior Python Developer",
        "description": "Need FastAPI, PostgreSQL, Azure, REST APIs, Docker, Python",
    },
    {
        "title": "Data Analyst",
        "description": "Need Excel, reporting, BI, SQL, dashboarding",
    },
]


def test_extract_candidate_profile_extracts_skill_keywords():
    profile = extract_candidate_profile(RESUME_TEXT)

    assert "python" in profile["keywords"]
    assert "fastapi" in profile["keywords"]
    assert "postgresql" in profile["keywords"]
    assert "azure" in profile["keywords"]
    assert profile["years_experience"] >= 3
    assert profile["domain"] == "data_ai"


def test_extract_candidate_profile_extracts_bi_and_data_engineering_keywords():
    profile = extract_candidate_profile(
        "Sr. Data Engineer with 11+ years experience in Tableau, Power BI, Domo, Talend, Snowflake, dbt, and R."
    )

    assert {"tableau", "power", "bi", "domo", "talend", "snowflake", "dbt", "r"}.issubset(
        set(profile["keywords"])
    )
    assert "power bi" in profile["phrases"]
    assert profile["domain"] == "data_ai"


def test_extract_candidate_profile_extracts_accounting_keywords():
    profile = extract_candidate_profile("Accounts payable, Xero, MYOB, SAP, Lightyear, payroll and reconciliations")

    assert {"accounts", "payable", "xero", "myob", "sap", "lightyear", "payroll", "reconciliations"}.issubset(
        set(profile["keywords"])
    )
    assert profile["domain"] == "accounting_finance"


def test_match_jobs_to_candidate_ranks_relevant_jobs_first():
    profile = extract_candidate_profile(RESUME_TEXT)
    matches = match_jobs_to_candidate(profile, JOBS)

    assert matches[0]["title"] == "Senior Python Developer"
    assert matches[0]["score"] > matches[1]["score"]


def test_analyze_job_fit_identifies_matched_and_missing_skills():
    profile = extract_candidate_profile("Data Engineer skilled in Python, SQL, and Tableau.")
    analysis = analyze_job_fit(
        profile,
        {
            "title": "Senior Data Engineer",
            "company": "Example Corp",
            "description": "Looking for Python, SQL, Tableau, Snowflake, and Databricks experience.",
        },
    )

    assert {"python", "sql", "tableau"}.issubset(set(analysis["matched_skills"]))
    assert {"snowflake", "databricks"}.issubset(set(analysis["missing_skills"]))
    assert 0 < analysis["coverage_pct"] < 100
    assert len(analysis["suggestions"]) >= 1
