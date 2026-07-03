"""Lever postings adapter (uses a handle as identifier)."""
import httpx
from .base import SourceAdapter

API = "https://api.lever.co/v0/postings/{handle}?mode=json"


class LeverAdapter(SourceAdapter):
    def fetch(self) -> list[dict]:
        url = API.format(handle=self.identifier)
        resp = httpx.get(url, timeout=20)
        resp.raise_for_status()
        postings = resp.json()

        out = []
        for p in postings:
            out.append({
                "source": "lever",
                "company": self.name,
                "title": p.get("text"),
                "location": (p.get("categories") or {}).get("location"),
                "source_url": p.get("hostedUrl"),
                "apply_url": p.get("applyUrl") or p.get("hostedUrl"),
                "description": p.get("descriptionPlain"),
                "posted_date": p.get("createdAt"),
            })
        return out