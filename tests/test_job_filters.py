from src.job_filters import is_target_australia_job
from src.job_scanner import scan_jobs_for_profile


def test_target_filter_accepts_relevant_australian_roles():
    jobs = [
        {"title": "Business Intelligence Analyst", "location": "Melbourne VIC"},
        {"title": "Data Analyst", "location": "Sydney NSW"},
        {"title": "Tableau Developer", "location": "Sydney"},
        {"title": "Power BI Specialist", "location": "Brisbane QLD"},
        {"title": "Data Engineer", "location": "Perth WA"},
        {"title": "Analytics Engineer", "location": "Remote - Australia"},
    ]

    assert all(is_target_australia_job(job) for job in jobs)


def test_target_filter_accepts_australian_ai_and_machine_learning_roles():
    jobs = [
        {"title": "AI Engineer", "location": "Sydney NSW"},
        {"title": "Machine Learning Engineer", "location": "Melbourne VIC"},
        {"title": "Data Scientist", "location": "Brisbane QLD"},
        {"title": "Generative AI Researcher", "location": "Canberra ACT"},
        {"title": "NLP Engineer", "location": "Perth WA"},
        {"title": "MLOps Specialist", "location": "Remote - Australia"},
        {"title": "Computer Vision Scientist", "location": "Adelaide SA"},
        {"title": "Machine Learning Engineer", "location": "Remote - Australia"},
        {"title": "Analytics Engineer", "location": "Canberra ACT"},
    ]

    assert all(is_target_australia_job(job) for job in jobs)


def test_target_filter_rejects_overseas_and_unrelated_roles():
    jobs = [
        {"title": "Power BI Analyst", "location": "London"},
        {"title": "Senior Business Analyst", "location": "Melbourne VIC"},
        {"title": "Software Engineer", "description": "Build internal tools", "location": "Sydney NSW"},
        {"title": "Data Engineer", "location": "Remote - United States"},
        {"title": "Data Engineer", "location": "Seattle, WA"},
        {"title": "Analytics Manager", "location": "Newcastle Upon Tyne, UK"},
        {
            "title": "Regional Sales Manager",
            "description": "Experience with business intelligence and Tableau is a plus.",
            "location": "Sydney NSW",
        },
        {
            "title": "Sales Manager",
            "description": "Familiarity with artificial intelligence is helpful.",
            "location": "Melbourne VIC",
        },
        {"title": "BI Analyst", "location": "Remote"},
    ]

    assert not any(is_target_australia_job(job) for job in jobs)


def test_scanner_persists_only_target_australian_jobs():
    import json

    payload = json.dumps({
        "jobs": [
            {
                "id": 1,
                "title": "Tableau Developer",
                "content": "Build dashboards",
                "absolute_url": "https://example.com/1",
                "location": {"name": "Melbourne"},
            },
            {
                "id": 2,
                "title": "Tableau Developer",
                "content": "Build dashboards",
                "absolute_url": "https://example.com/2",
                "location": {"name": "London"},
            },
            {
                "id": 3,
                "title": "Software Engineer",
                "content": "Build internal tools",
                "absolute_url": "https://example.com/3",
                "location": {"name": "Sydney NSW"},
            },
        ]
    })
    persisted = []

    scan_jobs_for_profile(
        {"keywords": ["tableau"]},
        [{
            "name": "Greenhouse test",
            "source_type": "greenhouse",
            "board_token": "example",
            "target_filter": True,
        }],
        fetcher=lambda url: payload,
        fetched_jobs_callback=persisted.extend,
    )

    assert [job["external_id"] for job in persisted] == ["1"]