"""Adzuna adapter — keyword search over aggregated job listings.

Like JSearch, this is a *search* source, not a company board: you give it keywords and it
returns matching jobs from Adzuna's aggregated database (thousands of sources), scoped to
a country. It adds salary data and an IT-jobs category filter, which JSearch doesn't
expose as cleanly. One entry with a list of keywords covers the whole market.

Config (in companies-backup.yaml):

    - name: Adzuna — Canada dev
      ats: adzuna
      queries: [developer, "software engineer", "flutter developer"]
      country: ca            # ISO code; default ca
      where: Toronto         # optional location filter
      category: it-jobs      # optional; restricts to IT roles
      results_per_page: 50   # max 50; default 50
      pages: 1               # pages per keyword; default 1
      active: true

It needs a free app_id + app_key in .env:
    ADZUNA_APP_ID=...
    ADZUNA_APP_KEY=...
(register at https://developer.adzuna.com/ — free.)
"""
import os

import httpx

from .base import SourceAdapter
from src.adapters.base import redact
from src.logs import log

API = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


class AdzunaAdapter(SourceAdapter):
    def fetch(self) -> list[dict]:
        app_id = os.environ.get("ADZUNA_APP_ID")
        app_key = os.environ.get("ADZUNA_APP_KEY")
        if not app_id or not app_key:
            raise RuntimeError("ADZUNA_APP_ID and ADZUNA_APP_KEY must be set in .env — "
                               "register free at https://developer.adzuna.com/")

        c = self.company
        queries = c.get("queries")
        if not queries:
            one = c.get("query")
            queries = [one] if one else ["software developer"]
        country = c.get("country", "ca")
        where = c.get("where", "")
        category = c.get("category", "")
        per_page = min(int(c.get("results_per_page", 50)), 50)
        pages = int(c.get("pages", 1))

        seen: set[str] = set()

        answered = False

        last_error = None
        out: list[dict] = []

        for kw in queries:
            for page in range(1, pages + 1):
                params = {
                    "app_id": app_id, "app_key": app_key,
                    "results_per_page": per_page, "what": kw,
                    "content-type": "application/json",
                }
                if where:
                    params["where"] = where
                if category:
                    params["category"] = category

                url = API.format(country=country, page=page)
                try:
                    r = httpx.get(url, params=params, timeout=30)
                    r.raise_for_status()
                    data = r.json()
                except Exception as e:
                    # redact(): the exception carries the request URL, and that URL
                    # carries app_id and app_key.
                    last_error = redact(e)
                    log.warning("[adzuna %s] '%s' page %s failed: %s",
                                self.name, kw, page, last_error)
                    break
                answered = True

                results = data.get("results", []) if isinstance(data, dict) else []
                if not results:
                    break

                for j in results:
                    jid = j.get("id") or j.get("redirect_url") or ""
                    if jid in seen:
                        continue
                    seen.add(jid)

                    loc = (j.get("location") or {}).get("display_name") or ""
                    company = (j.get("company") or {}).get("display_name") or "Unknown"
                    url_ = j.get("redirect_url") or ""
                    out.append({
                        # Adzuna gives numeric min/max; expose both for the salary floor.
                        "salary_min": _int(j.get("salary_min")),
                        "salary_max": _int(j.get("salary_max")),
                        "source": "adzuna",
                        "company": company,
                        "title": j.get("title"),
                        "location": loc,
                        "source_url": url_,
                        "apply_url": url_,
                        "description": j.get("description") or "",   # snippet only
                        "posted_date": j.get("created") or "",
                        "job_type": _job_type(j),
                    })

        if not answered:
            # Every request failed — a wrong key answers 401 to all of them. Returning
            # an empty list makes that look like "Adzuna had no matches", which sends
            # the reader off to widen their search terms when the account is the
            # problem. The health check keeps errors and quiet zeros apart on purpose.
            raise RuntimeError(f"{self.name}: every Adzuna request failed — {last_error}")

        return out


def _int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _job_type(j):
    if j.get("contract_time") == "full_time":
        return "Full-time"
    if j.get("contract_time") == "part_time":
        return "Part-time"
    if j.get("contract_type") == "contract":
        return "Contract"
    return None
