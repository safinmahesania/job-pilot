"""Remotive API — free, no key. Remote developer jobs."""
import httpx
from .base import SourceAdapter

API = "https://remotive.com/api/remote-jobs"


class RemotiveAdapter(SourceAdapter):
    def fetch(self) -> list[dict]:
        params = {}
        if self.company.get("query"):
            params["search"] = self.company["query"]
        limit = self.company.get("limit", 50)

        r = httpx.get(API, params=params, timeout=20)
        r.raise_for_status()
        jobs = r.json().get("jobs", [])[:limit]

        out = []
        for j in jobs:
            out.append({
                "source": "remotive",
                "scope": "global",
                "company": j.get("company_name"),
                "title": j.get("title"),
                "location": j.get("candidate_required_location") or "Remote",
                "source_url": j.get("url"),
                "apply_url": j.get("url"),
                "description": j.get("description"),   # HTML
                "posted_date": j.get("publication_date"),
            })
        return out