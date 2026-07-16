"""Workable public jobs adapter (uses the company subdomain as identifier).

Workable's careers widget is backed by an open endpoint — no auth — keyed by the
account slug (the subdomain in <identifier>.workable.com). It returns the published jobs
with title, location, and a hosted URL, which is all the pipeline needs to bring a job in
and score it; the full description is fetched later by following the link.
"""
import httpx

from .base import SourceAdapter

# The widget endpoint is the reliable public one (the v3 account API often needs a token).
API = "https://apply.workable.com/api/v1/widget/accounts/{slug}"


class WorkableAdapter(SourceAdapter):
    def fetch(self) -> list[dict]:
        slug = self.identifier
        resp = httpx.get(API.format(slug=slug), params={"details": "true"},
                         headers={"Accept": "application/json"}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        jobs = data.get("jobs", []) if isinstance(data, dict) else []

        out = []
        for j in jobs:
            city = j.get("city") or ""
            country = j.get("country") or j.get("countryCode") or ""
            location = ", ".join(p for p in (city, country) if p)
            if j.get("remote") or j.get("telecommuting"):
                location = (location + " (Remote)").strip() or "Remote"

            shortcode = j.get("shortcode") or ""
            url = (j.get("url")
                   or (f"https://apply.workable.com/{slug}/j/{shortcode}/"
                       if shortcode else ""))
            out.append({
                "source": "workable",
                "company": self.name,
                "title": j.get("title"),
                "location": location,
                "source_url": url,
                "apply_url": url,
                "description": j.get("description") or "",   # widget details=true may include it
                "posted_date": j.get("published_on") or j.get("created_at") or "",
                "job_type": j.get("employment_type"),
            })
        return out
