import re


_AUSTRALIA_LOCATION = re.compile(
    r"\b(?:australia|australian|melbourne|sydney|brisbane|perth|adelaide|canberra|"
    r"hobart|darwin|geelong|newcastle|wollongong|gold coast|sunshine coast|"
    r"cairns|townsville|ballarat|bendigo|toowoomba|victoria|new south wales|"
    r"queensland|western australia|south australia|tasmania|northern territory|"
    r"australian capital territory|vic|nsw|qld|tas)\b",
    re.IGNORECASE,
)
_ROLE_CONTEXT = re.compile(
    r"\b(?:data|analytics?|reporting|insights|etl|warehouse|bi|software|machine\s+learning|ai|ml)\b",
    re.IGNORECASE,
)
_OUTSIDE_AUSTRALIA = re.compile(
    r"\b(?:united kingdom|\buk\b|england|scotland|wales|ireland|united states|\busa\b|"
    r"canada|new zealand|germany|france|spain|italy|netherlands|singapore|india|"
    r"japan|china|switzerland|sweden|norway|denmark|finland|poland|portugal)\b",
    re.IGNORECASE,
)
_TARGET_ROLE = re.compile(
    r"\b(?:business\s+intelligence|tableau|power\s*bi|powerbi|domo|talend|looker|snowflake|databricks|dbt|"
    r"data\s+engineer(?:ing)?|analytics\s+engineer|etl\s+(?:engineer|developer|architect)|"
    r"data\s+(?:platform|warehouse)\s+(?:engineer|developer|architect)|"
    r"artificial\s+intelligence|machine\s+learning|deep\s+learning|generative\s+ai|genai|"
    r"large\s+language\s+models?|llms?|natural\s+language\s+processing|nlp|"
    r"computer\s+vision|data\s+(?:analyst|scient(?:ist|ience))|mlops)\b|"
    r"\b(?:ai|ml)\s*(?:[/&-]\s*(?:ai|ml)\s*)?"
    r"(?:engineer|developer|researcher|scientist|specialist|consultant|architect|lead|manager|analyst|product\s+manager)\b|"
    r"\b(?:engineer|developer|researcher|scientist|specialist|consultant|architect|lead|manager|analyst)\s+(?:ai|ml)\b|"
    r"\bbi\s+(?:analyst|developer|engineer|specialist|consultant|architect|lead|manager)\b|"
    r"\b(?:analyst|developer|engineer|specialist|consultant|architect|lead|manager)\s+bi\b",
    re.IGNORECASE,
)
_ACCOUNTING_ROLE = re.compile(
    r"\b(?:accountant|accounting|accounts\s+(?:payable|receivable|officer|assistant|clerk)|"
    r"bookkeeper|bookkeeping|payroll\s+(?:officer|specialist|administrator|manager)|"
    r"finance\s+(?:officer|analyst|manager|coordinator|assistant)|"
    r"financial\s+(?:accountant|analyst|controller)|tax\s+accountant|assistant\s+accountant|"
    r"credit\s+controller|accounts\s+all\s+rounder)\b",
    re.IGNORECASE,
)


def is_target_australia_job(job: dict, domain: str | None = None) -> bool:
    """Return whether a posting is Australian and related to the configured target roles."""
    if not is_australia_job(job):
        return False

    title_and_category = " ".join(
        str(job.get(field) or "") for field in ("title", "category")
    )
    description = str(job.get("description") or "")

    if domain == "accounting_finance":
        if _ACCOUNTING_ROLE.search(title_and_category):
            return True
        if _TARGET_ROLE.search(title_and_category):
            return True
        return bool(_ACCOUNTING_ROLE.search(description) or (_ROLE_CONTEXT.search(title_and_category) and _TARGET_ROLE.search(description)))

    if _TARGET_ROLE.search(title_and_category):
        return True

    return bool(_ROLE_CONTEXT.search(title_and_category) and _TARGET_ROLE.search(description))


def is_australia_job(job: dict) -> bool:
    """Return whether the posting has a recognized Australian location."""
    location = str(job.get("location") or "")
    if _OUTSIDE_AUSTRALIA.search(location):
        return False
    return bool(_AUSTRALIA_LOCATION.search(location))
