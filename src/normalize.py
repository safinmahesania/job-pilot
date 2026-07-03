"""Map raw adapter records into the common schema + a dedupe hash."""
import hashlib
import re


def _clean(text: str | None) -> str:
    return (text or "").strip().lower()


def dedupe_hash(company: str, title: str, location: str | None) -> str:
    raw = f"{_clean(company)}|{_clean(title)}|{_clean(location)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"<[^>]+>", " ", text).replace("&nbsp;", " ").strip()


def normalize(raw: dict) -> dict:
    company = raw.get("company")
    title = raw.get("title")
    location = raw.get("location")
    return {
        "dedupe_hash": dedupe_hash(company, title, location),
        "source": raw.get("source"),
        "source_url": raw.get("source_url"),
        "apply_url": raw.get("apply_url") or raw.get("source_url"),
        "title": title,
        "company": company,
        "location": location,
        "remote": 1 if location and "remote" in location.lower() else 0,
        "description": strip_html(raw.get("description")),
        "posted_date": raw.get("posted_date"),
    }