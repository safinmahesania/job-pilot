"""RemoteOK API — free JSON, remote jobs. Needs a User-Agent."""
import httpx
from .base import SourceAdapter

API = "https://remoteok.com/api"


class RemoteOKAdapter(SourceAdapter):
    def fetch(self) -> list[dict]:
        r = httpx.get(API, headers={"User-Agent": "Mozilla/5.0 (JobPilot)"}, timeout=20)
        r.raise_for_status()
        data = r.json()
        query = (self.company.get("query") or "").lower()
        out = []
        for j in data:
            if not isinstance(j, dict) or not j.get("position"):
                continue  # first element is legal/metadata
            title = j.get("position")
            tags = " ".join(j.get("tags", [])).lower()
            if query and query not in title.lower() and query not in tags:
                continue
            url = j.get("url") or j.get("apply_url")
            out.append({
                "source": "remoteok",
                "company": j.get("company"),
                "title": title,
                "location": j.get("location") or "Remote",
                "source_url": url,
                "apply_url": j.get("apply_url") or url,
                "description": j.get("description"),
                "posted_date": j.get("date"),
                "job_type": None,
            })
        return out