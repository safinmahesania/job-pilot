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
                rec = {
                    "source": f"workday:{tenant}",
                    "company": self.name,
                    "title": p.get("title"),
                    "location": p.get("locationsText"),
                    "source_url": job_url,
                    "apply_url": job_url,
                    "description": p.get("title"),
                    "posted_date": p.get("postedOn"),
                    "job_type": None,
                    "deadline": None,
                }
                if c.get("details", True) and path:
                    rec.update({k: v for k, v in self._detail(base, tenant, site, path).items() if v})
                    time.sleep(0.3)  # politeness
                out.append(rec)

            offset += per_page
            if offset >= data.get("total", 0):
                break
            time.sleep(0.5)
        return out

    def _detail(self, base, tenant, site, path):
        url = f"{base}/wday/cxs/{tenant}/{site}{path}"
        try:
            r = httpx.get(url, headers={"Accept": "application/json",
                                        "Content-Type": "application/json"}, timeout=25)
            r.raise_for_status()
            info = r.json().get("jobPostingInfo", {})
            return {
                "description": info.get("jobDescription"),
                "job_type": info.get("timeType"),
                "deadline": info.get("endDate"),
                "posted_date": info.get("startDate"),
            }
        except Exception:
            return {}