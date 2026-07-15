"""The Muse public API — free, no key. Great for entry-level/internship + location."""
import time
import httpx
from .base import SourceAdapter
from src.logs import log

API = "https://www.themuse.com/api/public/jobs"


class TheMuseAdapter(SourceAdapter):
    def fetch(self) -> list[dict]:
        c = self.company
        max_pages = c.get("max_pages", 2)
        out = []
        for page in range(max_pages):
            params = {"page": page}
            if c.get("category"):
                params["category"] = c["category"]
            if c.get("level"):
                params["level"] = c["level"]
            if c.get("location"):
                params["location"] = c["location"]
            try:
                r = httpx.get(API, params=params, timeout=20)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                log.warning("[The Muse] page %s failed: %s", page, e)
                break

            for j in data.get("results", []):
                locs = j.get("locations") or []
                out.append({
                    "source": "themuse",
                    "company": (j.get("company") or {}).get("name"),
                    "title": j.get("name"),
                    "location": ", ".join(l.get("name", "") for l in locs) or None,
                    "source_url": (j.get("refs") or {}).get("landing_page"),
                    "apply_url": (j.get("refs") or {}).get("landing_page"),
                    "description": j.get("contents"),   # HTML
                    "posted_date": j.get("publication_date"),
                })

            if page + 1 >= data.get("page_count", 1):
                break
            time.sleep(0.5)   # politeness
        return out