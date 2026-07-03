"""Workday adapter — uses the CXS JSON endpoint (no HTML scraping)."""
import time
import httpx
from .base import SourceAdapter


class WorkdayAdapter(SourceAdapter):
    def fetch(self) -> list[dict]:
        c = self.company
        tenant = c["tenant"]
        host = c.get("host", "wd3")
        site = c["site"]
        base = f"https://{tenant}.{host}.myworkdayjobs.com"
        api = f"{base}/wday/cxs/{tenant}/{site}/jobs"

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        max_pages = c.get("max_pages", 3)
        out, offset, per_page = [], 0, 20

        for _ in range(max_pages):
            payload = {
                "appliedFacets": {},
                "limit": per_page,
                "offset": offset,
                "searchText": c.get("query", ""),
            }
            try:
                r = httpx.post(api, json=payload, headers=headers, timeout=25)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                print(f"  [Workday {tenant}] offset {offset} failed: {e}")
                break

            postings = data.get("jobPostings", [])
            if not postings:
                break

            for p in postings:
                path = p.get("externalPath", "")
                job_url = f"{base}/{site}{path}" if path else base
                out.append({
                    "source": f"workday:{tenant}",
                    "company": self.name,
                    "title": p.get("title"),
                    "location": p.get("locationsText"),
                    "source_url": job_url,
                    "apply_url": job_url,
                    "description": p.get("title"),   # list view me full JD nahi hoti
                    "posted_date": p.get("postedOn"),
                })

            offset += per_page
            if offset >= data.get("total", 0):
                break
            time.sleep(0.5)
        return out