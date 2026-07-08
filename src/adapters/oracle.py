"""Oracle Recruiting Cloud (CE) adapter — JSON API, no auth."""
import time
import httpx
from .base import SourceAdapter


class OracleAdapter(SourceAdapter):
    def fetch(self) -> list[dict]:
        c = self.company
        host = c["host"]          # e.g. jpmc.fa.oraclecloud.com
        site = c["site"]          # e.g. CX_1001
        base = f"https://{host}/hcmRestApi/resources/latest"
        max_pages = c.get("max_pages", 3)
        per = 25
        headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 (JobPilot)"}
        out = []

        for page in range(max_pages):
            offset = page * per
            kw = c.get("query", "")
            kw_part = f",keyword={kw.replace(' ', '%20')}" if kw else ""
            url = (f"{base}/recruitingCEJobRequisitions?onlyData=true"
                   f"&expand=requisitionList.secondaryLocations,flexFieldsFacet.values"
                   f"&finder=findReqs;siteNumber={site},limit={per},offset={offset}{kw_part}")
            try:
                r = httpx.get(url, headers=headers, timeout=30)
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                print(f"  [Oracle {site}] offset {offset} failed: {e}")
                break

            items = data.get("items", [])
            reqs = items[0].get("requisitionList", []) if items else []
            if not reqs:
                break

            for j in reqs:
                jid = j.get("Id")
                job_url = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{jid}"
                out.append({
                    "source": f"oracle:{self.name}",
                    "company": self.name,
                    "title": j.get("Title"),
                    "location": j.get("PrimaryLocation"),
                    "source_url": job_url,
                    "apply_url": job_url,
                    "description": j.get("ExternalResponsibilitiesStr") or j.get("ShortDescriptionStr") or "",
                    "posted_date": j.get("PostedDate"),
                    "deadline": j.get("PostingEndDate"),
                    "job_type": j.get("WorkerType") or j.get("JobType"),
                })

            if not data.get("hasMore"):
                break
            time.sleep(0.4)
        return out