"""WeWorkRemotely — free RSS feed, remote jobs."""
import xml.etree.ElementTree as ET
import httpx
from .base import SourceAdapter

FEED = "https://weworkremotely.com/categories/remote-programming-jobs.rss"


class WeWorkRemotelyAdapter(SourceAdapter):
    def fetch(self) -> list[dict]:
        url = self.company.get("feed", FEED)
        r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0 (JobPilot)"}, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        out = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            if ":" in title:
                company, job_title = title.split(":", 1)
            else:
                company, job_title = "Unknown", title
            link = (item.findtext("link") or "").strip()
            out.append({
                "source": "weworkremotely",
                "company": company.strip() or "Unknown",
                "title": job_title.strip(),
                "location": "Remote",
                "source_url": link,
                "apply_url": link,
                "description": item.findtext("description") or "",
                "posted_date": item.findtext("pubDate") or "",
                "job_type": None,
            })
        return out