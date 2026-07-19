"""JSearch adapter — keyword search across the whole web (Google for Jobs).

Unlike the company adapters (Greenhouse, Lever, …) which each fetch one employer's board,
JSearch is a *search* source: you give it keywords and it returns matching jobs pulled
from LinkedIn, Indeed, Glassdoor, ZipRecruiter and the rest, via Google for Jobs. So one
entry can cover the entire market for a set of roles.

Config (in companies-backup.yaml):

    - name: JSearch — dev roles
      ats: jsearch
      queries: [developer, "software engineer", "flutter developer", "java developer"]
      country: ca            # ISO code; default ca
      pages: 1               # pages per query (10 results each); default 1
      active: true

It needs a free API key in .env:  JSEARCH_API_KEY=...
(from RapidAPI's JSearch, or OpenWeb Ninja — both expose the same x-api-key endpoint.)
"""
import re
import os

import httpx

from .base import SourceAdapter
from src.adapters.base import redact
from src.logs import log

# OpenWeb Ninja's direct endpoint (same shape as the RapidAPI one, simpler auth).
API = "https://api.openwebninja.com/jsearch/search"
RAPIDAPI = "https://jsearch.p.rapidapi.com/search"

#: What a RapidAPI key looks like: one long alphanumeric run with `msh` and `jsn` in
#: it. OpenWeb Ninja issues a different shape, so the key itself says which host it is
#: for and nobody has to remember to write `host: rapidapi` in the source.
_RAPIDAPI_KEY_SHAPE = re.compile(r"^[a-z0-9]{6,}msh[a-z0-9]+jsn[a-z0-9]+$", re.I)


def _why(response, error) -> str:
    """The server's own explanation, not just the status line.

    A status code names the category; the body names the cause. RapidAPI answers 403
    with "You are not subscribed to this API", 404 with which host it could not find —
    and throwing that away leaves a bare "404 Not Found" that could mean six things and
    sends you searching the web instead of reading the answer you were already given.
    """
    body = ""
    try:
        body = (response.text or "").strip().replace("\n", " ")[:200]
    except Exception:
        pass
    return redact(f"{error}{f' — server said: {body}' if body else ''}")


class JSearchAdapter(SourceAdapter):
    def fetch(self) -> list[dict]:
        key = os.environ.get("JSEARCH_API_KEY")
        if not key:
            raise RuntimeError("JSEARCH_API_KEY is not set in .env — get a free key from "
                               "RapidAPI's JSearch or openwebninja.com")

        c = self.company
        # One or many keywords. `queries` (a list) is preferred; `query` (a string) is
        # accepted too, so a single-keyword source still works.
        queries = c.get("queries")
        if not queries:
            one = c.get("query")
            queries = [one] if one else ["software developer"]
        country = c.get("country", "ca")
        pages = int(c.get("pages", 1))
        location = c.get("location", "")

        # RapidAPI and OpenWeb Ninja serve the same API on different hosts, with
        # different auth headers. Sending a RapidAPI key to OpenWeb Ninja gets a 401
        # that says nothing about the real problem — which is not a wrong key, but a
        # key pointed at the wrong door.
        #
        # The two issue visibly different keys, so this reads the key rather than
        # asking. A RapidAPI key is one long alphanumeric run with `msh` and `jsn`
        # inside it (…4f2amsh8e1c…p1b9…jsn6d5c…); OpenWeb Ninja's has neither. An
        # explicit `host:` in the source still wins, for the day that stops being true.
        configured = c.get("host", "").lower()
        if configured:
            use_rapid = configured == "rapidapi"
        else:
            use_rapid = bool(_RAPIDAPI_KEY_SHAPE.match(key.strip()))

        base = RAPIDAPI if use_rapid else API
        host_name = "RapidAPI" if use_rapid else "OpenWeb Ninja"
        headers = ({"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}
                   if use_rapid else {"x-api-key": key})

        seen: set[str] = set()

        answered = False

        last_error = None
        out: list[dict] = []

        for kw in queries:
            q = f"{kw} in {location}" if location else kw
            for page in range(1, pages + 1):
                params = {"query": q, "country": country, "page": page, "num_pages": 1}
                r = None
                try:
                    r = httpx.get(base, params=params, headers=headers, timeout=30)
                    r.raise_for_status()
                    data = r.json()
                except Exception as e:
                    last_error = _why(r, e) if r is not None else redact(e)
                    log.warning("[jsearch %s] '%s' page %s failed: %s",
                                self.name, kw, page, last_error)
                    break
                answered = True

                jobs = data.get("data", []) if isinstance(data, dict) else []
                if not jobs:
                    break

                for j in jobs:
                    url = j.get("job_apply_link") or j.get("job_google_link") or ""
                    job_id = j.get("job_id") or url
                    if job_id in seen:
                        continue
                    seen.add(job_id)

                    city = j.get("job_city") or ""
                    state = j.get("job_state") or ""
                    ctry = j.get("job_country") or ""
                    location_str = ", ".join(p for p in (city, state, ctry) if p)
                    if j.get("job_is_remote"):
                        location_str = (location_str + " (Remote)").strip() or "Remote"

                    out.append({
                        "source": "jsearch",
                        "company": j.get("employer_name") or "Unknown",
                        "title": j.get("job_title"),
                        "location": location_str,
                        "source_url": j.get("job_google_link") or url,
                        "apply_url": url,
                        "description": j.get("job_description") or "",
                        "posted_date": (j.get("job_posted_at_datetime_utc")
                                        or j.get("job_posted_at") or ""),
                        "job_type": j.get("job_employment_type"),
                        "remote": 1 if j.get("job_is_remote") else 0,
                    })

        if not answered:
            # Every request failed — a key that is valid for a different host answers
            # 401 to all of them. An empty list would report that as "JSearch had no
            # matches" and hide the real cause.
            other = "OpenWeb Ninja" if use_rapid else "RapidAPI"
            raise RuntimeError(
                f"{self.name}: every JSearch request failed against {host_name} — "
                f"{last_error}. If your key is from {other}, set `host: "
                f"{'openwebninja' if use_rapid else 'rapidapi'}` on this source."
            )

        return out
