import json
import hashlib
import math
import os
import re
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from src.job_filters import is_target_australia_job
from src.profile_builder import tokenize_text


def fetch_html(url: str, timeout: int = 20) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Accept-Language": "en-AU,en;q=0.9",
        },
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type()
            body = response.read()

            if content_type == "text/html":
                return body.decode("utf-8", errors="ignore")

            return body.decode("utf-8", errors="ignore")
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:400].strip()
        message = (
            f"Adzuna API rejected the request with HTTP {error.code}. "
            "Check your saved credentials and source filters."
        )
        if detail:
            message += f" Details: {detail}"
        raise RuntimeError(message) from error
    except URLError as error:
        raise RuntimeError(f"Unable to reach the remote job source: {error.reason}") from error


def clean_text(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value or "")
    return unescape(cleaned).strip()


def extract_jobs_from_html(html_text: str) -> list[dict]:
    soup = BeautifulSoup(html_text, "html.parser")
    jobs = []
    seen_titles = set()

    for heading in soup.find_all(["h2", "h3"]):
        title = clean_text(heading.get_text(" ", strip=True))
        if not title:
            continue

        if any(keyword in title.lower() for keyword in ["perform a job search", "related searches", "read more", "receive new jobs", "seeking", "search results"]):
            continue

        if title in seen_titles:
            continue
        seen_titles.add(title)

        description_parts = []
        current = heading.next_sibling
        while current is not None:
            if getattr(current, "name", None) in {"h2", "h3"}:
                break
            if getattr(current, "name", None) is None:
                text = clean_text(str(current))
                if text:
                    description_parts.append(text)
            else:
                text = clean_text(current.get_text(" ", strip=True))
                if text:
                    description_parts.append(text)
            current = current.next_sibling

        description = " ".join(description_parts)
        if not description:
            description = title

        url = ""
        link = heading.find_parent("a")
        if link is not None:
            href = link.get("href")
            if href:
                url = urljoin("https://au.seek.com", href)

        if not url:
            parent = heading.find_parent(["article", "li", "div", "section"])
            if parent is not None:
                job_link = parent.find("a", href=True)
                if job_link is not None and "/job/" in job_link.get("href", ""):
                    url = urljoin("https://au.seek.com", job_link["href"])

        jobs.append({
            "title": title,
            "description": description,
            "url": url,
        })

    return jobs


def _xml_child_text(element, *names: str) -> str:
    for child in list(element):
        local_name = child.tag.rsplit("}", 1)[-1].lower()
        if local_name in names:
            return " ".join(child.itertext()).strip()
    return ""


def _feed_date(value: str) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError, OverflowError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except (TypeError, ValueError):
            return value.strip()


def _normalize_json_feed_job(item: dict) -> dict:
    company = item.get("company") or item.get("company_name") or item.get("companyName") or ""
    if isinstance(company, dict):
        company = company.get("display_name") or company.get("name") or ""

    location = (
        item.get("location")
        or item.get("candidate_required_location")
        or item.get("jobGeo")
        or item.get("jobLocation")
        or ""
    )
    if isinstance(location, dict):
        location = location.get("display_name") or location.get("name") or ""

    category = item.get("category") or item.get("jobIndustry") or item.get("tags") or ""
    if isinstance(category, list):
        category = ", ".join(str(value) for value in category)

    description = (
        item.get("description")
        or item.get("summary")
        or item.get("jobDescription")
        or item.get("jobExcerpt")
        or ""
    )
    return {
        "external_id": str(item.get("id") or item.get("slug") or item.get("jobSlug") or item.get("url") or "").strip(),
        "title": str(item.get("title") or item.get("jobTitle") or "").strip(),
        "description": _clean_html(str(description)),
        "url": str(item.get("redirect_url") or item.get("link") or item.get("url") or "").strip(),
        "company": str(company).strip(),
        "location": str(location).strip(),
        "posted_at": _feed_date(str(item.get("created_at") or item.get("publication_date") or item.get("pubDate") or item.get("posted_at") or "")),
        "category": str(category).strip(),
        "salary_min": item.get("salary_min") or item.get("salaryMin"),
        "salary_max": item.get("salary_max") or item.get("salaryMax"),
        "raw_json": item,
    }


def parse_feed_entries(
    feed_text: str,
    source_type: str = "rss",
    source: dict | None = None,
) -> list[dict]:
    source = source or {}
    if source_type.lower() == "rss":
        root = ET.fromstring(feed_text)
        items = []
        feed_items = [
            element for element in root.iter()
            if element.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}
        ]
        for item in feed_items:
            title = _xml_child_text(item, "title")
            link = _xml_child_text(item, "link")
            if not link:
                link_element = next(
                    (child for child in list(item) if child.tag.rsplit("}", 1)[-1].lower() == "link"),
                    None,
                )
                link = str(link_element.get("href", "")) if link_element is not None else ""
            description = _xml_child_text(item, "description", "summary", "content")
            categories = [
                " ".join(category.itertext()).strip()
                for category in list(item)
                if category.tag.rsplit("}", 1)[-1].lower() == "category"
            ]
            job = {
                "external_id": _xml_child_text(item, "guid", "id") or link,
                "title": title,
                "url": link.strip(),
                "description": _clean_html(description),
                "company": _xml_child_text(item, "creator", "author"),
                "location": _xml_child_text(item, "location", "joblocation", "region"),
                "posted_at": _feed_date(_xml_child_text(item, "pubdate", "published", "updated", "date")),
                "category": ", ".join(value for value in categories if value),
            }
            if job["title"] or job["url"] or job["description"]:
                items.append(job)
        return items

    if source_type.lower() in {"json", "adzuna"}:
        payload = json.loads(feed_text)
        if source_type.lower() == "adzuna":
            items = payload.get("results", [])
        elif isinstance(payload, list):
            items = payload
        else:
            items_path = source.get("items_path")
            if items_path:
                items = payload
                for path_part in items_path.split("."):
                    items = items.get(path_part, []) if isinstance(items, dict) else []
            else:
                items = next(
                    (payload[key] for key in ("items", "data", "jobs", "results") if isinstance(payload.get(key), list)),
                    [],
                )
        if source_type.lower() == "adzuna":
            return [_normalize_adzuna_job(item) for item in items]
        return [_normalize_json_feed_job(item) for item in items]

    return []


def _clean_html(value: str) -> str:
    return clean_text(BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True))


def _normalize_greenhouse_job(item: dict) -> dict:
    location = item.get("location") or {}
    departments = item.get("departments") or []
    return {
        "external_id": str(item.get("id", "")).strip(),
        "title": str(item.get("title", "")).strip(),
        "description": _clean_html(item.get("content", "")),
        "url": str(item.get("absolute_url", "")).strip(),
        "company": str(item.get("company_name", "")).strip(),
        "location": str(location.get("name", "")).strip(),
        "posted_at": str(item.get("first_published") or item.get("updated_at") or "").strip() or None,
        "category": ", ".join(
            str(department.get("name", "")).strip()
            for department in departments
            if department.get("name")
        ),
        "raw_json": item,
    }


def _normalize_lever_job(item: dict, source_name: str) -> dict:
    categories = item.get("categories") or {}
    created_at = item.get("createdAt")
    posted_at = None
    if created_at is not None:
        try:
            posted_at = datetime.fromtimestamp(float(created_at) / 1000, timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            posted_at = None
    salary = item.get("salaryRange") or {}
    return {
        "external_id": str(item.get("id", "")).strip(),
        "title": str(item.get("text", "")).strip(),
        "description": str(item.get("descriptionPlain") or item.get("openingPlain") or "").strip(),
        "url": str(item.get("hostedUrl") or item.get("applyUrl") or "").strip(),
        "company": source_name,
        "location": str(categories.get("location") or ", ".join(categories.get("allLocations") or [])).strip(),
        "posted_at": posted_at,
        "category": str(categories.get("team") or categories.get("department") or "").strip(),
        "salary_min": salary.get("min"),
        "salary_max": salary.get("max"),
        "contract_time": str(categories.get("commitment") or "").strip(),
        "raw_json": item,
    }


def fetch_greenhouse_jobs(source: dict, fetcher=fetch_html) -> list[dict]:
    board_token = str(source.get("board_token", "")).strip()
    if not board_token:
        raise ValueError(f"Greenhouse source '{source.get('name', 'unknown')}' requires a board_token.")
    endpoint = source.get(
        "endpoint",
        "https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs",
    ).format(board_token=board_token)
    separator = "&" if "?" in endpoint else "?"
    payload = json.loads(fetcher(f"{endpoint}{separator}content=true"))
    return [_normalize_greenhouse_job(item) for item in payload.get("jobs", [])]


def fetch_lever_jobs(source: dict, fetcher=fetch_html) -> list[dict]:
    site = str(source.get("site", "")).strip()
    if not site:
        raise ValueError(f"Lever source '{source.get('name', 'unknown')}' requires a site.")
    region = str(source.get("region", "global")).lower()
    host = "api.eu.lever.co" if region == "eu" else "api.lever.co"
    endpoint = str(source.get("endpoint") or f"https://{host}/v0/postings/{{site}}").format(site=site)
    limit = max(1, int(source.get("results_per_page", 100)))
    max_pages = max(1, int(source.get("max_pages_per_scan", 10)))
    jobs = []

    for page in range(max_pages):
        params = urlencode({"mode": "json", "skip": page * limit, "limit": limit})
        payload = json.loads(fetcher(f"{endpoint}?{params}"))
        page_jobs = payload if isinstance(payload, list) else payload.get("postings", [])
        jobs.extend(_normalize_lever_job(item, str(source.get("name", site))) for item in page_jobs)
        if len(page_jobs) < limit:
            break
    else:
        if len(page_jobs) == limit:
            raise RuntimeError(
                f"Lever scan exceeded the configured page limit ({max_pages}); "
                "increase max_pages_per_scan or narrow the source."
            )

    return jobs


def _normalize_adzuna_job(item: dict) -> dict:
    company = item.get("company") or {}
    location = item.get("location") or {}
    category = item.get("category") or {}
    return {
        "external_id": str(item.get("id", "")).strip(),
        "title": str(item.get("title", "")).strip(),
        "description": str(item.get("description", "")).strip(),
        "url": str(item.get("redirect_url") or "").strip(),
        "company": str(company.get("display_name", "")).strip(),
        "location": str(location.get("display_name", "")).strip(),
        "posted_at": str(item.get("created") or "").strip() or None,
        "category": str(category.get("label") or category.get("tag") or "").strip(),
        "salary_min": item.get("salary_min"),
        "salary_max": item.get("salary_max"),
        "salary_is_predicted": item.get("salary_is_predicted"),
        "contract_time": item.get("contract_time", ""),
        "contract_type": item.get("contract_type", ""),
        "raw_json": item,
    }


def _query_for_source(source: dict, profile: dict) -> str:
    profile_keywords = profile.get("keywords", [])
    keyword_counts = profile.get("keyword_counts", {})
    ranked_keywords = sorted(
        profile_keywords,
        key=lambda keyword: (-keyword_counts.get(keyword, 0), keyword),
    )
    return str(source.get("what") or " ".join(ranked_keywords[:5])).strip()


def build_search_key(source: dict, profile: dict) -> str:
    search_definition = {
        "source": source.get("name", "unknown"),
        "source_type": (source.get("source_type") or "rss").lower(),
        "country": source.get("country", ""),
        "what": _query_for_source(source, profile),
        "where": source.get("where", ""),
    }
    encoded = json.dumps(search_definition, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def calculate_max_days_old(
    latest_posted_at: str | None,
    initial_days: int = 7,
    overlap_days: int = 1,
    now: datetime | None = None,
) -> int:
    if not latest_posted_at:
        return max(1, int(initial_days))

    try:
        posted_at = datetime.fromisoformat(latest_posted_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return max(1, int(initial_days))
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    age_days = math.ceil(max(0, (current_time - posted_at).total_seconds()) / 86400)
    return max(1, age_days + max(0, int(overlap_days)))


def build_adzuna_url(
    source: dict,
    profile: dict,
    page: int = 1,
    max_days_old: int | None = None,
) -> str:
    app_id = source.get("app_id") or os.getenv(source.get("app_id_env", "ADZUNA_APP_ID"))
    app_key = source.get("app_key") or os.getenv(source.get("app_key_env", "ADZUNA_APP_KEY"))
    if not app_id or not app_key:
        raise ValueError("Adzuna requires ADZUNA_APP_ID and ADZUNA_APP_KEY environment variables.")

    country = source.get("country", "au")
    endpoint = source.get(
        "endpoint",
        f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}",
    ).format(country=country, page=page)
    if source.get("endpoint") and "{page}" not in source["endpoint"]:
        endpoint = re.sub(r"/\d+(?=$)", f"/{page}", endpoint)
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": source.get("results_per_page", 20),
        "sort_by": "date",
        "sort_dir": "down",
        "max_days_old": max_days_old or source.get("initial_days", 7),
    }
    query = _query_for_source(source, profile)
    if query:
        params["what"] = query
    if source.get("where"):
        params["where"] = source["where"]

    return f"{endpoint}?{urlencode(params)}"


def fetch_adzuna_jobs(
    source: dict,
    profile: dict,
    fetcher=fetch_html,
    max_days_old: int = 7,
) -> list[dict]:
    jobs = []
    results_per_page = int(source.get("results_per_page", 20))
    max_pages = max(1, int(source.get("max_pages_per_scan", 10)))
    search_key = build_search_key(source, profile)

    for page in range(1, max_pages + 1):
        payload = json.loads(fetcher(build_adzuna_url(source, profile, page, max_days_old)))
        page_results = payload.get("results", [])
        for item in page_results:
            job = _normalize_adzuna_job(item)
            job["source"] = source.get("name", "unknown")
            job["search_key"] = search_key
            jobs.append(job)

        total_results = payload.get("count")
        if not page_results or len(page_results) < results_per_page:
            break
        if total_results is not None and page * results_per_page >= int(total_results):
            break
    else:
        if total_results is None or max_pages * results_per_page < int(total_results):
            raise RuntimeError(
                f"Adzuna scan exceeded the configured page limit ({max_pages}); "
                "increase max_pages_per_scan or narrow the search. Scan checkpoint was not advanced."
            )

    return jobs


def compute_job_score(profile: dict, job: dict) -> dict:
    candidate_keywords = set(profile.get("keywords", [])) if profile else set()
    title_tokens = set(tokenize_text(job.get("title", "")))
    description_text = " ".join(
        (job.get("description", ""), job.get("company", ""), job.get("location", ""))
    )
    description_tokens = set(tokenize_text(description_text))
    title_matches = sorted(candidate_keywords.intersection(title_tokens))
    description_matches = sorted(candidate_keywords.intersection(description_tokens))
    overlap = sorted(set(title_matches).union(description_matches))
    if not overlap:
        return {
            "score": 0,
            "matches": [],
            "title_matches": [],
            "seniority_adjustment": 0,
        }
    description_only_matches = set(description_matches).difference(title_matches)
    score = len(title_matches) * 15 + len(description_only_matches) * 5
    title_lower = job.get("title", "").lower()
    years_experience = profile.get("years_experience", 0) if profile else 0
    seniority_adjustment = 0
    if years_experience >= 5 and any(
        level in title_lower for level in ("intern", "graduate", "entry level", "junior")
    ):
        seniority_adjustment = -10
    elif years_experience >= 5 and any(
        level in title_lower for level in ("senior", "lead", "principal", "head of")
    ):
        seniority_adjustment = 5
    score = max(0, score + seniority_adjustment)
    return {
        "score": score,
        "matches": overlap,
        "title_matches": title_matches,
        "seniority_adjustment": seniority_adjustment,
    }


def attach_profile_scores(profile: dict, jobs: list[dict]) -> list[dict]:
    enriched = []
    for job in jobs:
        scoring = compute_job_score(profile, job)
        item = dict(job)
        item.update(scoring)
        enriched.append(item)
    return enriched


def score_jobs_for_profile(profile: dict, jobs: list[dict]) -> list[dict]:
    results = []
    for job in jobs:
        scoring = compute_job_score(profile, job)
        if not scoring["matches"]:
            continue

        results.append({
            "title": job.get("title", "Untitled Job"),
            "score": scoring["score"],
            "matches": scoring["matches"],
            "title_matches": scoring["title_matches"],
            "seniority_adjustment": scoring["seniority_adjustment"],
            "source": job.get("source", "unknown"),
            "url": job.get("url", ""),
            "posted_at": job.get("posted_at"),
            "description": job.get("description", ""),
            "company": job.get("company", ""),
            "location": job.get("location", ""),
            "category": job.get("category", ""),
            "salary_min": job.get("salary_min"),
            "salary_max": job.get("salary_max"),
            "contract_time": job.get("contract_time", ""),
            "contract_type": job.get("contract_type", ""),
        })

    return sorted(results, key=lambda item: item["score"], reverse=True)


def scan_jobs_for_profile(
    profile: dict,
    sources: list[dict],
    fetcher=fetch_html,
    max_days_old_by_search_key: dict[str, int] | None = None,
    fetched_jobs_callback=None,
    scan_completed_callback=None,
    source_error_callback=None,
) -> list[dict]:
    fetched_jobs = []
    max_days_old_by_search_key = max_days_old_by_search_key or {}
    domain = profile.get("domain") if isinstance(profile, dict) else None

    for source in sources:
        source_type = (source.get("source_type") or "rss").lower()
        if not source.get("enabled", True):
            continue
        search_key = build_search_key(source, profile)
        try:
            if source_type == "adzuna":
                jobs = fetch_adzuna_jobs(
                    source,
                    profile,
                    fetcher=fetcher,
                    max_days_old=max_days_old_by_search_key.get(
                        search_key,
                        int(source.get("initial_days", 7)),
                    ),
                )
            elif source_type == "greenhouse":
                jobs = fetch_greenhouse_jobs(source, fetcher=fetcher)
                for job in jobs:
                    job["source"] = source.get("name", "Greenhouse")
                    job["search_key"] = search_key
            elif source_type == "lever":
                jobs = fetch_lever_jobs(source, fetcher=fetcher)
                for job in jobs:
                    job["source"] = source.get("name", "Lever")
                    job["search_key"] = search_key
            else:
                feed_url = source.get("feed_url") or source.get("search_url") or ""
                feed_text = fetcher(feed_url)
                if not feed_text:
                    continue
                jobs = parse_feed_entries(feed_text, source_type, source=source)
                if not jobs:
                    jobs = extract_jobs_from_html(feed_text)

                for job in jobs:
                    job.setdefault("external_id", job.get("url") or job.get("title", "unknown"))
                    job.setdefault("posted_at", None)
                    job["company"] = job.get("company") or source.get("name", "")
                    job["source"] = source.get("name", "unknown")
                    job["search_key"] = search_key
        except (RuntimeError, json.JSONDecodeError, ET.ParseError, URLError) as error:
            if isinstance(error, RuntimeError) and "page limit" in str(error).lower():
                raise
            failure = {
                "source": source.get("name", "unknown"),
                "error": str(error) or "Source request failed.",
            }
            if source_error_callback is not None:
                source_error_callback(failure)
            continue

        if source.get("target_filter"):
            jobs = [job for job in jobs if is_target_australia_job(job, domain=domain)]

        if fetched_jobs_callback is not None:
            fetched_jobs_callback(jobs)
        if source_type == "adzuna" and scan_completed_callback is not None:
            latest_posted_at = max(
                (job["posted_at"] for job in jobs if job.get("posted_at")),
                default=None,
            )
            scan_completed_callback(search_key, latest_posted_at, len(jobs))
        fetched_jobs.extend(jobs)

    return score_jobs_for_profile(profile, fetched_jobs)
