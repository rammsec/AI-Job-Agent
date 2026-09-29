import re
from collections import Counter


def normalize_token(token: str) -> str:
    cleaned = token.strip().lower().replace("/", " ").replace("-", " ")
    cleaned = re.sub(r"^[^a-z0-9]+|[^a-z0-9]+$", "", cleaned)
    cleaned = cleaned.replace("#", "")
    return cleaned


def tokenize_text(text: str) -> list[str]:
    text = text.lower()
    tokens = re.findall(r"[a-z0-9+.#]+", text)
    normalized = [normalize_token(token) for token in tokens]
    return [token for token in normalized if token]


def extract_candidate_profile(resume_text: str) -> dict:
    tokens = tokenize_text(resume_text)
    keyword_counts = Counter(tokens)

    skill_keywords = {
        "python",
        "fastapi",
        "django",
        "flask",
        "sql",
        "postgresql",
        "mysql",
        "mongodb",
        "docker",
        "kubernetes",
        "azure",
        "aws",
        "gcp",
        "machine",
        "learning",
        "ml",
        "nlp",
        "api",
        "rest",
        "data",
        "analysis",
        "javascript",
        "typescript",
        "react",
        "java",
        "csharp",
        "node",
        "html",
        "css",
        "linux",
        "git",
        "ci",
        "cd",
        "accounting",
        "accounts",
        "payable",
        "receivable",
        "bookkeeping",
        "xero",
        "myob",
        "payroll",
        "reconciliation",
        "reconciliations",
        "invoice",
        "invoices",
        "excel",
        "gst",
        "bas",
        "finance",
        "budgeting",
        "audit",
        "tax",
    }

    keywords = sorted({
        token for token in tokens
        if token in skill_keywords or token.replace(" ", "") in skill_keywords
    })

    years_experience = 0
    numbers = re.findall(r"(\d+)\+?\s*(?:years?|yrs?)[^\n]*", resume_text.lower())
    if numbers:
        years_experience = max(int(value) for value in numbers)

    return {
        "keywords": keywords,
        "years_experience": years_experience,
        "raw_tokens": tokens,
        "keyword_counts": dict(keyword_counts),
    }


def match_jobs_to_candidate(profile: dict, jobs: list[dict]) -> list[dict]:
    candidate_keywords = set(profile["keywords"])
    matches = []

    for job in jobs:
        text = f"{job.get('title', '')} {job.get('description', '')}".lower()
        tokens = tokenize_text(text)
        job_keywords = set(tokens)
        overlap = sorted(candidate_keywords.intersection(job_keywords))
        score = len(overlap) * 10 + (profile["years_experience"] * 2)

        matches.append({
            "title": job.get("title", "Untitled Job"),
            "score": score,
            "matches": overlap,
        })

    return sorted(matches, key=lambda item: item["score"], reverse=True)
