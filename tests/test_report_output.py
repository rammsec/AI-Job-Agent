from src.job_report import build_report, run_daily_scan


PROFILE = {
    "candidate_name": "Sample Candidate",
    "keywords": ["python", "fastapi", "azure", "sql"],
    "years_experience": 5,
}


SOURCES = [
    {
        "name": "demo-rss",
        "source_type": "rss",
        "feed_url": "https://example.com/rss",
    }
]

RSS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Senior Python Developer</title>
      <link>https://example.com/jobs/1</link>
      <description>FastAPI, Azure, SQL and Python experience</description>
    </item>
    <item>
      <title>Data Analyst</title>
      <link>https://example.com/jobs/2</link>
      <description>Excel, reporting, Power BI</description>
    </item>
  </channel>
</rss>
"""


def test_build_report_formats_top_matches():
    matches = [
        {"title": "Senior Python Developer", "score": 90, "matches": ["python", "fastapi", "azure"]},
        {"title": "Data Analyst", "score": 40, "matches": ["sql"]},
    ]

    report = build_report(matches)

    assert "Senior Python Developer" in report
    assert "90" in report
    assert "python" in report.lower()


def test_run_daily_scan_persists_and_returns_matches(tmp_path):
    db_path = tmp_path / "daily_scan.db"
    matches = run_daily_scan(
        PROFILE,
        SOURCES,
        fetcher=lambda url: RSS_FEED,
        db_path=db_path,
    )

    assert matches[0]["title"] == "Senior Python Developer"
    assert matches[0]["score"] > 0
