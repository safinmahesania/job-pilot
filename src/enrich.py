"""Fetch the full description for an Adzuna job from where its link points.

Adzuna's API returns a truncated snippet — the first few lines and no more — because it
is built to send traffic to Adzuna rather than hand over complete records. The full text
is on the page `redirect_url` leads to. Scoring a job on half its description is scoring
half a job: the requirements, seniority and stack tend to live below the fold the snippet
cuts off.

But not every link is worth following, and this is deliberately conservative about which
are. Three destinations are fetched, all of them safe to read at this scale:

  - Adzuna's own detail page, where it has copied the full posting. Following its own
    redirect service is what that service is for.
  - Lever and Greenhouse — applicant tracking systems whose postings are exposed through
    public, structured JSON APIs (api.lever.co, boards-api.greenhouse.io). No HTML to
    scrape, no bot detection, no CAPTCHA; hitting these is ordinary and expected.

Everything else — Workday, LinkedIn, Indeed, a company's own careers page — is left
alone. Those either render the description with JavaScript a plain fetch can't see, or are
sites one shouldn't crawl. A job whose link isn't one of the three keeps its snippet and,
by the no-description rule in run.py, stays unscored rather than getting a number off half
its text.

Why the ban risk is low: an Adzuna run's links fan out across many different destinations,
so no single site is hammered — each is read once or twice, the way a person opening a tab
would. The three allowed hosts are Adzuna's own service and two public APIs built to be
queried.
"""
from __future__ import annotations

import re

import httpx

from src.logs import log
from src.normalize import strip_html
from src.paths import MIN_DESCRIPTION_CHARS

#: A real posting is longer than this. Below it, whatever came back is a wall, an
#: expired-job stub, or a redirect page — not a description — so the snippet we already
#: had is the better of two bad options and is kept.
_MIN_ENRICHED_CHARS = 400

#: Browser-like agent: some pages answer bare clients with a block page, and there is
#: nothing to hide — this reads a public posting.
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    "Accept": "text/html,application/json,application/xhtml+xml",
}

# jobs.lever.co/{company}/{id}  ->  the two path parts identify the posting.
# jobs.lever.co/{company}/{id}  ->  the two path parts identify the posting. The id is a
# UUID on most postings but not all, so match any non-slash run rather than hex only.
_LEVER = re.compile(r"jobs\.lever\.co/([^/?#]+)/([^/?#]+)", re.I)
# boards.greenhouse.io/{token}/jobs/{id}  or  job-boards.greenhouse.io/...
_GREENHOUSE = re.compile(
    r"(?:job-)?boards\.greenhouse\.io/(?:embed/job_app\?for=)?([^/?]+)"
    r".*?jobs?[/=](\d+)", re.I)


def is_enrichable(job: dict) -> bool:
    """Whether this job is one we fetch a fuller description for.

    Adzuna only — it is the source with truncated snippets. Company boards, Lever and
    Greenhouse jobs that arrive through their OWN adapters already carry full text; this
    is for the Adzuna job whose link happens to point at one of the fetchable
    destinations.
    """
    if job.get("source") != "adzuna":
        return False
    url = job.get("source_url") or job.get("apply_url") or ""
    return bool(_destination(url))


def _destination(url: str) -> str | None:
    """Which fetch strategy this URL qualifies for, or None to leave the job alone.

    Only the three known-safe destinations return a strategy. Anything else — Workday,
    LinkedIn, a company careers page — returns None and keeps its snippet.
    """
    if not url:
        return None
    if _LEVER.search(url):
        return "lever"
    if _GREENHOUSE.search(url):
        return "greenhouse"
    if "adzuna." in url:
        return "adzuna"
    return None


def _get(url: str, timeout: float) -> httpx.Response | None:
    try:
        r = httpx.get(url, headers=_HEADERS, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
        return r
    except Exception as e:
        log.warning("[enrich] fetch failed for %s: %s", url[:80], str(e)[:100])
        return None


def _from_lever(url: str, timeout: float) -> str | None:
    """Lever's public JSON: api.lever.co/v0/postings/{company}/{id}?mode=json.

    Structured, so the description comes out clean rather than scraped off a rendered
    page — and no different from what Lever's own adapter reads.
    """
    m = _LEVER.search(url)
    if not m:
        return None
    api = f"https://api.lever.co/v0/postings/{m[1]}/{m[2]}?mode=json"
    r = _get(api, timeout)
    if not r:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    parts = [strip_html(data.get("descriptionPlain") or data.get("description") or "")]
    for lst in data.get("lists", []) or []:
        parts.append(strip_html(lst.get("text") or ""))
    return "\n".join(p for p in parts if p).strip() or None


def _from_greenhouse(url: str, timeout: float) -> str | None:
    """Greenhouse's public JSON: boards-api.greenhouse.io/v1/boards/{token}/jobs/{id}."""
    m = _GREENHOUSE.search(url)
    if not m:
        return None
    api = f"https://boards-api.greenhouse.io/v1/boards/{m[1]}/jobs/{m[2]}"
    r = _get(api, timeout)
    if not r:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    return strip_html(data.get("content") or "").strip() or None


def _from_adzuna(url: str, timeout: float) -> str | None:
    """Adzuna's link, followed to wherever it lands.

    Adzuna's `redirect_url` is a /land/ad/ hop through its own domain to the real
    posting. Following it can land three useful places: an Adzuna detail page, or —
    because employers post through them — a Lever or Greenhouse page. So follow the
    redirect, look at where it actually ended up, and if that final URL is a Lever or
    Greenhouse posting, read it through their clean JSON API instead of scraping the
    rendered page. Otherwise take the page's text.

    Anything that lands on Indeed, LinkedIn or a JS-rendered careers page yields little —
    a plain fetch sees an empty shell — and the short result is rejected by the caller,
    leaving the job unscored rather than scored on a fragment.
    """
    r = _get(url, timeout)
    if not r:
        return None

    # Where did the redirect actually end up? Re-route to a structured API when it landed
    # somewhere we can read cleanly.
    final = str(r.url)
    if _LEVER.search(final):
        text = _from_lever(final, timeout)
        if text:
            return text
    if _GREENHOUSE.search(final):
        text = _from_greenhouse(final, timeout)
        if text:
            return text

    # Otherwise, the text of the page we landed on (an Adzuna detail page, or a simple
    # employer page). Strip to readable lines and keep them.
    text = strip_html(r.text) or ""
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip() or None


def full_description(job: dict, *, timeout: float = 12.0) -> str | None:
    """The full posting text, or None if it could not be had from an allowed source.

    None is a real answer: the caller leaves such a job unscored rather than score it on
    the snippet, on the same principle that a job with no description at all is left
    alone. A guess dressed as a score is worse than an honest gap.
    """
    url = job.get("source_url") or job.get("apply_url") or ""
    strategy = _destination(url)
    if strategy == "lever":
        text = _from_lever(url, timeout)
    elif strategy == "greenhouse":
        text = _from_greenhouse(url, timeout)
    elif strategy == "adzuna":
        text = _from_adzuna(url, timeout)
    else:
        return None

    if not text or len(text) < _MIN_ENRICHED_CHARS:
        return None
    return text


def enrich_if_needed(job: dict) -> bool:
    """Replace an Adzuna snippet with the full posting, in place.

    Returns True if the description was replaced. Leaves the job untouched (and returns
    False) when its link is not one of the three fetchable destinations, when the snippet
    is already long enough to be the real thing, or when the fetch came back with nothing
    usable — in which case the caller sees a still-short description and skips scoring it.
    """
    if not is_enrichable(job):
        return False
    # Already substantial AND not visibly truncated: leave it. Adzuna cuts snippets with
    # a trailing "…" even when they run past the length floor, so a long description that
    # ends in an ellipsis is still a fragment worth replacing.
    current = (job.get("description") or "").strip()
    truncated = current.endswith("…") or current.endswith("...")
    if len(current) >= _MIN_ENRICHED_CHARS and not truncated:
        return False

    full = full_description(job)
    if not full or len(full) < MIN_DESCRIPTION_CHARS:
        return False

    job["description"] = full
    return True
