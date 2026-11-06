import re
from collections import Counter


DATA_AI_KEYWORDS = {
    "python",
    "r",
    "sql",
    "postgresql",
    "mysql",
    "mongodb",
    "sqlite",
    "snowflake",
    "databricks",
    "bigquery",
    "redshift",
    "synapse",
    "Clickhouse".lower(),
    "tableau",
    "powerbi",
    "power",
    "bi",
    "domo",
    "talend",
    "looker",
    "qlik",
    "metabase",
    "superset",
    "ssrs",
    "ssis",
    "ssas",
    "dax",
    "dbt",
    "airflow",
    "prefect",
    "dagster",
    "spark",
    "pyspark",
    "hadoop",
    "kafka",
    "flink",
    "etl",
    "elt",
    "warehouse",
    "warehousing",
    "lakehouse",
    "data",
    "analysis",
    "analytics",
    "reporting",
    "dashboard",
    "dashboards",
    "insights",
    "pipeline",
    "pipelines",
    "modeling",
    "modelling",
    "governance",
    "machine",
    "learning",
    "ml",
    "ai",
    "genai",
    "llm",
    "llms",
    "nlp",
    "rag",
    "mlops",
    "pytorch",
    "tensorflow",
    "keras",
    "scikit",
    "pandas",
    "numpy",
    "scipy",
    "matplotlib",
    "plotly",
    "fastapi",
    "django",
    "flask",
    "api",
    "rest",
    "graphql",
    "docker",
    "kubernetes",
    "terraform",
    "azure",
    "aws",
    "gcp",
    "javascript",
    "typescript",
    "react",
    "java",
    "csharp",
    "php",
    "node",
    "html",
    "css",
    "linux",
    "git",
    "ci",
    "cd",
    "gtm",
    "ga4",
    "crm",
}

ACCOUNTING_FINANCE_KEYWORDS = {
    "accounting",
    "accountant",
    "accounts",
    "payable",
    "receivable",
    "bookkeeping",
    "xero",
    "myob",
    "sap",
    "lightyear",
    "netsuite",
    "quickbooks",
    "payroll",
    "reconciliation",
    "reconciliations",
    "reconcile",
    "invoice",
    "invoices",
    "invoicing",
    "billing",
    "excel",
    "gst",
    "bas",
    "ias",
    "fbt",
    "superannuation",
    "finance",
    "financial",
    "budgeting",
    "forecasting",
    "variance",
    "audit",
    "tax",
    "taxation",
    "cpa",
    "ca",
    "asa",
    "erp",
    "ledger",
    "debtors",
    "creditors",
    "accruals",
    "journals",
    "compliance",
}

SKILL_KEYWORDS = DATA_AI_KEYWORDS | ACCOUNTING_FINANCE_KEYWORDS

MULTI_WORD_PHRASES = (
    "power bi",
    "business intelligence",
    "data engineer",
    "data engineering",
    "analytics engineer",
    "data analyst",
    "data scientist",
    "data science",
    "machine learning",
    "deep learning",
    "generative ai",
    "large language model",
    "natural language processing",
    "computer vision",
    "google analytics",
    "tag management",
    "data warehouse",
    "data platform",
    "accounts payable",
    "accounts receivable",
    "bank reconciliation",
    "financial reporting",
    "month end",
    "general ledger",
)


def normalize_token(token: str) -> str:
    cleaned = token.strip().lower().replace("/", " ").replace("-", " ")
    cleaned = re.sub(r"^[^a-z0-9]+|[^a-z0-9]+$", "", cleaned)
    cleaned = cleaned.replace("#", "")
    return cleaned


def tokenize_text(text: str) -> list[str]:
    text = (text or "").lower()
    tokens = re.findall(r"[a-z0-9+.#]+", text)
    normalized = [normalize_token(token) for token in tokens]
    return [token for token in normalized if token]


def extract_phrases(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", (text or "").lower())
    return sorted(
        phrase
        for phrase in MULTI_WORD_PHRASES
        if re.search(rf"\b{re.escape(phrase)}\b", cleaned)
    )


def detect_profile_domain(keywords: list[str] | set[str]) -> str:
    keyword_set = {str(k).lower() for k in (keywords or [])}
    data_hits = len(keyword_set.intersection(DATA_AI_KEYWORDS))
    accounting_hits = len(keyword_set.intersection(ACCOUNTING_FINANCE_KEYWORDS - {"excel"}))
    if accounting_hits > data_hits and accounting_hits >= 2:
        return "accounting_finance"
    return "data_ai"


def extract_candidate_profile(resume_text: str) -> dict:
    tokens = tokenize_text(resume_text)
    keyword_counts = Counter(tokens)

    keywords = sorted({
        token for token in tokens
        if token in SKILL_KEYWORDS or token.replace(" ", "") in SKILL_KEYWORDS
    })
    phrases = extract_phrases(resume_text)

    years_experience = 0
    numbers = re.findall(r"(\d+)\+?\s*(?:years?|yrs?)[^\n]*", (resume_text or "").lower())
    if numbers:
        years_experience = max(int(value) for value in numbers if int(value) <= 50)

    domain = detect_profile_domain(keywords)

    return {
        "keywords": keywords,
        "phrases": phrases,
        "domain": domain,
        "years_experience": years_experience,
        "raw_tokens": tokens,
        "keyword_counts": dict(keyword_counts),
    }


def match_jobs_to_candidate(profile: dict, jobs: list[dict]) -> list[dict]:
    candidate_keywords = set(profile.get("keywords", []))
    matches = []

    for job in jobs:
        text = f"{job.get('title', '')} {job.get('description', '')}".lower()
        tokens = tokenize_text(text)
        job_keywords = set(tokens)
        overlap = sorted(candidate_keywords.intersection(job_keywords))
        score = len(overlap) * 10 + (profile.get("years_experience", 0) * 2)

        matches.append({
            "title": job.get("title", "Untitled Job"),
            "score": score,
            "matches": overlap,
        })

    return sorted(matches, key=lambda item: item["score"], reverse=True)


def analyze_job_fit(profile: dict, job: dict) -> dict:
    """Compare a job against the candidate profile and return matched skills, missing skills, and tailoring tips."""
    candidate_keywords = set(profile.get("keywords", []))
    title_text = str(job.get("title") or "")
    desc_text = str(job.get("description") or "")
    combined_text = f"{title_text} {desc_text}"

    job_tokens = set(tokenize_text(combined_text))
    job_skills = sorted(job_tokens.intersection(SKILL_KEYWORDS))
    matched_skills = sorted(candidate_keywords.intersection(job_skills))
    missing_skills = sorted(set(job_skills).difference(candidate_keywords))

    job_phrases = extract_phrases(combined_text)
    candidate_phrases = set(profile.get("phrases") or [])
    missing_phrases = [phrase for phrase in job_phrases if phrase not in candidate_phrases]

    total_required = len(job_skills)
    coverage_pct = round((len(matched_skills) / total_required) * 100) if total_required else 100

    suggestions = []
    if matched_skills:
        top_matched = ", ".join(matched_skills[:6])
        suggestions.append(
            f"Highlight your direct experience with {top_matched} in the top summary of your resume for '{title_text}'."
        )
    if missing_skills:
        top_missing = ", ".join(missing_skills[:5])
        suggestions.append(
            f"Add or explicitly name {top_missing} in your project bullet points if you have hands-on exposure to them."
        )
    if missing_phrases:
        suggestions.append(
            f"Include exact terminology from the posting such as {', '.join(missing_phrases[:3])} for ATS keyword alignment."
        )
    if not suggestions:
        suggestions.append(
            f"Your profile already covers the detected keywords in '{title_text}'. Emphasize measurable impact and outcomes."
        )

    return {
        "title": title_text,
        "company": str(job.get("company") or ""),
        "coverage_pct": coverage_pct,
        "matched_skills": matched_skills,
        "missing_skills": missing_skills,
        "job_phrases": job_phrases,
        "suggestions": suggestions,
    }
