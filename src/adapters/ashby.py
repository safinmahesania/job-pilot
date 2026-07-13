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

            # Ashby returns compensation only when the company publishes it.
            # Prefer the structured range; fall back to the summary string.
            comp = j.get("compensation") or {}
            salary = None
            tiers = comp.get("compensationTiers") or []
            for tier in tiers:
                components = tier.get("components") or []
                for c in components:
                    if (c.get("compensationType") or "").lower() == "salary":
                        salary = {"min": c.get("minValue"), "max": c.get("maxValue")}
                        break
                if salary:
                    break
            if not salary:
                salary = comp.get("compensationTierSummary")   # e.g. "$90K - $120K"

            out.append({
                "salary": salary,
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