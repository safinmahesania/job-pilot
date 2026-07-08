"""Jobspresso — free RSS feed, curated remote dev jobs."""
import xml.etree.ElementTree as ET
import httpx
from .base import SourceAdapter

FEED = "https://jobspresso.co/remote-work/feed/"


class JobspressoAdapter(SourceAdapter):
    def fetch(self) -> list[dict]:
        url = self.company.get("feed", FEED)
        r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0 (JobPilot)"}, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        out = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = item.findtext("description") or ""
            # Jobspresso title format aksar "Job Title at Company"
            company = "Unknown"
            job_title = title
            if " at " in title:
                job_title, company = title.rsplit(" at ", 1)
            out.append({
                "source": "jobspresso",
                "scope": "global",
                "company": company.strip() or "Unknown",
                "title": job_title.strip(),
                "location": "Remote",
                "source_url": link,
                "apply_url": link,
                "description": desc,
                "posted_date": item.findtext("pubDate") or "",
                "job_type": None,
            })
        return out