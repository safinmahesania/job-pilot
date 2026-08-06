"""TeamTailor job board adapter.

TeamTailor sites expose a public JSON feed at
``https://<company>.teamtailor.com/jobs.json`` — no auth. The subdomain is the token
(e.g. Vention's board is vention.na.teamtailor.com, token "vention.na"). Some boards sit
on a regional subdomain like "vention.na"; pass whatever comes before ".teamtailor.com"
as the identifier.
"""
import httpx

from .base import SourceAdapter

FEED = "https://{token}.teamtailor.com/jobs.json"


class TeamTailorAdapter(SourceAdapter):
    def fetch(self) -> list[dict]:
        token = self.identifier
        r = httpx.get(
            FEED.format(token=token),
            headers={"User-Agent": "Mozilla/5.0 (JobPilot)", "Accept": "application/json"},
            timeout=25,
        )
        r.raise_for_status()
        data = r.json()
        # The feed is either a list of jobs or an object with a "jobs" list.
        jobs = data if isinstance(data, list) else data.get("jobs", [])

        out = []
        for j in jobs:
            loc = j.get("location") or ""
            if isinstance(loc, dict):
                loc = loc.get("city") or loc.get("name") or ""
            if j.get("remote-status") in ("fully", "hybrid") and "remote" not in loc.lower():
                loc = (loc + " (Remote)").strip()

            url = j.get("careersite-job-url") or j.get("url") or j.get("apply-url")
            out.append({
                "source": "teamtailor:" + str(token).lower(),
                "company": self.name,
                "title": j.get("title"),
                "location": loc,
                "source_url": url,
                "apply_url": j.get("apply-url") or url,
                "description": j.get("body") or j.get("pitch") or "",
                "posted_date": j.get("created-at") or j.get("updated-at"),
                "job_type": None,
            })
        return out
