from src.profile_builder import extract_candidate_profile, match_jobs_to_candidate


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


def test_extract_candidate_profile_extracts_accounting_keywords():
    profile = extract_candidate_profile("Accounts payable, Xero, MYOB, payroll and reconciliations")

    assert {"accounts", "payable", "xero", "myob", "payroll", "reconciliations"}.issubset(
        set(profile["keywords"])
    )


def test_match_jobs_to_candidate_ranks_relevant_jobs_first():
    profile = extract_candidate_profile(RESUME_TEXT)
    matches = match_jobs_to_candidate(profile, JOBS)

    assert matches[0]["title"] == "Senior Python Developer"
    assert matches[0]["score"] > matches[1]["score"]
