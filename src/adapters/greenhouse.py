"""Greenhouse public board adapter (uses a board token as identifier)."""
import httpx
from .base import SourceAdapter

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


class GreenhouseAdapter(SourceAdapter):
    def fetch(self) -> list[dict]:
        url = API.format(token=self.identifier)
        resp = httpx.get(url, timeout=20)
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])

        out = []
        for j in jobs:
            out.append({
                "source": "greenhouse",
                "company": self.name,
                "title": j.get("title"),
                "location": (j.get("location") or {}).get("name"),
                "source_url": j.get("absolute_url"),
                "apply_url": j.get("absolute_url"),   # Greenhouse: same link
                "description": j.get("content"),       # HTML
                "posted_date": j.get("updated_at"),
            })
        return out