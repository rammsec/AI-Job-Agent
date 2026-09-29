import json
import os
import re
from html import unescape
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from src.profile_builder import tokenize_text


def fetch_html(url: str, timeout: int = 20) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Accept-Language": "en-AU,en;q=0.9",
        },
    )

    with urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get_content_type()
        body = response.read()

        if content_type == "text/html":
            return body.decode("utf-8", errors="ignore")

        return body.decode("utf-8", errors="ignore")


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


def parse_feed_entries(feed_text: str, source_type: str = "rss") -> list[dict]:
    if source_type.lower() == "rss":
        root = ET.fromstring(feed_text)
        items = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            if title or link or description:
                items.append({
                    "title": title,
                    "url": link,
                    "description": description,
                })
        return items

    if source_type.lower() in {"json", "adzuna"}:
        payload = json.loads(feed_text)
        items = payload.get("results", []) if source_type.lower() == "adzuna" else payload.get("items", [])
        return [
            {
                "title": str(item.get("title", "")).strip(),
                "url": str(item.get("redirect_url") or item.get("link") or item.get("url") or "").strip(),
                "description": " ".join(filter(None, [
                    str(item.get("description") or item.get("summary") or "").strip(),
                    str((item.get("company") or {}).get("display_name", "")).strip(),
                    str((item.get("location") or {}).get("display_name", "")).strip(),
                ])),
            }
            for item in items
        ]

    return []


def build_adzuna_url(source: dict, profile: dict) -> str:
    app_id = source.get("app_id") or os.getenv(source.get("app_id_env", "ADZUNA_APP_ID"))
    app_key = source.get("app_key") or os.getenv(source.get("app_key_env", "ADZUNA_APP_KEY"))
    if not app_id or not app_key:
        raise ValueError("Adzuna requires ADZUNA_APP_ID and ADZUNA_APP_KEY environment variables.")

    country = source.get("country", "au")
    endpoint = source.get(
        "endpoint",
        f"https://api.adzuna.com/v1/api/jobs/{country}/search/1",
    )
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": source.get("results_per_page", 20),
    }
    profile_keywords = profile.get("keywords", [])
    keyword_counts = profile.get("keyword_counts", {})
    ranked_keywords = sorted(
        profile_keywords,
        key=lambda keyword: (-keyword_counts.get(keyword, 0), keyword),
    )
    query = source.get("what") or " ".join(ranked_keywords[:5])
    if query:
        params["what"] = query
    if source.get("where"):
        params["where"] = source["where"]

    return f"{endpoint}?{urlencode(params)}"


def scan_jobs_for_profile(profile: dict, sources: list[dict], fetcher=fetch_html) -> list[dict]:
    candidate_keywords = set(profile.get("keywords", []))
    results = []

    for source in sources:
        source_type = (source.get("source_type") or "rss").lower()
        if not source.get("enabled", True):
            continue
        feed_url = (
            build_adzuna_url(source, profile)
            if source_type == "adzuna"
            else source.get("feed_url") or source.get("search_url") or ""
        )
        feed_text = fetcher(feed_url)
        if not feed_text:
            continue

        jobs = parse_feed_entries(feed_text, source_type)
        if not jobs:
            jobs = extract_jobs_from_html(feed_text)

        for job in jobs:
            text = f"{job.get('title', '')} {job.get('description', '')}".lower()
            tokens = set(tokenize_text(text))
            overlap = sorted(candidate_keywords.intersection(tokens))
            score = len(overlap) * 10 + (profile.get("years_experience", 0) * 2)

            results.append({
                "title": job.get("title", "Untitled Job"),
                "score": score,
                "matches": overlap,
                "source": source.get("name", "unknown"),
                "url": job.get("url", feed_url),
            })

    return sorted(results, key=lambda item: item["score"], reverse=True)
