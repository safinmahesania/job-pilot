"""SmartRecruiters public postings adapter (uses the company identifier as the slug).

SmartRecruiters exposes an open postings API — no auth, no key — keyed by the company's
identifier (the slug in jobs.smartrecruiters.com/<identifier>). It pages through results
with offset/limit, so this walks the pages until they run out or a page limit is hit.
"""
import httpx

from .base import SourceAdapter

API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
JOB_URL = "https://jobs.smartrecruiters.com/{slug}/{posting_id}"
_PAGE = 100


class SmartRecruitersAdapter(SourceAdapter):
    def fetch(self) -> list[dict]:
        slug = self.identifier
        max_pages = int(self.company.get("max_pages", 5))
        out = []

        for page in range(max_pages):
            params = {"limit": _PAGE, "offset": page * _PAGE}
            resp = httpx.get(API.format(slug=slug), params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("content", [])
            if not content:
                break

            for j in content:
                loc = j.get("location") or {}
                city = loc.get("city") or ""
                country = loc.get("country") or ""
                location = ", ".join(p for p in (city, country) if p)
                if loc.get("remote"):
                    location = (location + " (Remote)").strip()

                posting_id = j.get("id") or j.get("uuid") or ""
                out.append({
                    "source": "smartrecruiters",
                    "company": self.name,
                    "title": j.get("name"),
                    "location": location,
                    "source_url": (j.get("ref")
                                   or JOB_URL.format(slug=slug, posting_id=posting_id)),
                    "apply_url": (j.get("applyUrl")
                                  or JOB_URL.format(slug=slug, posting_id=posting_id)),
                    "description": "",       # postings list is titles only; body via detail
                    "posted_date": j.get("releasedDate") or j.get("createdOn") or "",
                    "job_type": (j.get("typeOfEmployment") or {}).get("label"),
                })

            # last (short) page -> stop
            if len(content) < _PAGE:
                break

        return out
