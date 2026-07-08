"""Ashby job board adapter — free JSON API, no auth. (e.g. Cohere, Wealthsimple)"""
import httpx
from .base import SourceAdapter

API = "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"


class AshbyAdapter(SourceAdapter):
    def fetch(self) -> list[dict]:
        token = self.identifier
        r = httpx.get(API.format(token=token),
                      headers={"User-Agent": "Mozilla/5.0 (JobPilot)", "Accept": "application/json"},
                      timeout=25)
        r.raise_for_status()
        jobs = r.json().get("jobs", [])

        out = []
        for j in jobs:
            # Ashby location: string ya nested; best-effort
            loc = j.get("location") or ""
            if not loc and j.get("address"):
                loc = (j["address"].get("postalAddress", {}) or {}).get("addressLocality", "")
            if j.get("isRemote") and "remote" not in loc.lower():
                loc = (loc + " (Remote)").strip()

            url = j.get("jobUrl") or j.get("applyUrl")
            out.append({
                "source": "ashby:" + self.name.lower().replace(" ", ""),
                "company": self.name,
                "title": j.get("title"),
                "location": loc,
                "source_url": url,
                "apply_url": j.get("applyUrl") or url,
                "description": j.get("descriptionHtml") or j.get("descriptionPlain") or "",
                "posted_date": j.get("publishedAt") or j.get("publishedDate"),
                "job_type": j.get("employmentType"),
            })
        return out