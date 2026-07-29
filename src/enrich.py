"""Fetch the full description for an Adzuna job from the page it links to.

Adzuna's API is built to send traffic to Adzuna, not to hand over complete records, so
its `description` field is a truncated snippet — the first few lines and no more. Scoring
a job on half its description is scoring half a job: the requirements, the seniority, the
tech stack all tend to live below the fold that the snippet cuts off.

The full text is on the page `redirect_url` points to. So for an Adzuna job that is about
to be scored — and only then, so the fetch is spent on jobs that matter rather than on the
hundreds the fit gate is about to drop — this follows that link and reads the page.

Why this is reasonable where scraping a whole board is not: each Adzuna job links to a
different destination (a different employer, a different board), so a run's requests fan
out across many domains rather than hammering one. There is no site here being crawled;
there are many pages being read one apiece.

Only Adzuna. Company boards, LinkedIn and Indeed listings already arrive with their full
text or are better fetched by hand, and following those at scale is exactly the crawl this
avoids.
"""
from __future__ import annotations

import httpx

from src.logs import log
from src.normalize import strip_html
from src.paths import MIN_DESCRIPTION_CHARS

#: A real posting is longer than this. Below it, whatever came back is a cookie wall, a
#: "this job has expired" stub, or a redirect page — not a description — so the snippet we
#: already had is the better of two bad options and is kept.
_MIN_ENRICHED_CHARS = 400

#: Adzuna's link redirects through its own domain before landing on the employer's page;
#: follow it. A browser-like agent because some boards answer bare clients with a block
#: page, and there is nothing to hide here — this is reading a public posting.
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
}


def is_enrichable(job: dict) -> bool:
    """Whether this job is one we fetch a fuller description for.

    Adzuna only, and only when there is a link to follow. Everything else keeps the
    description it arrived with.
    """
    return (job.get("source") == "adzuna"
            and bool(job.get("source_url") or job.get("apply_url")))


def _extract(html: str) -> str:
    """The longest run of readable text on the page.

    Job pages wrap the description in wildly different markup, so rather than guess at a
    selector per board, strip the whole page to text and trust that the description is the
    largest block of prose on it — which, on a job posting, it is. Crude, but it does not
    have to know one site's DOM from another's.
    """
    text = strip_html(html) or ""
    # Collapse the run-together whitespace that stripping tags leaves behind.
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def full_description(job: dict, *, timeout: float = 12.0) -> str | None:
    """The full posting text, or None if it could not be had.

    None is a real answer: the caller leaves such a job unscored rather than score it on
    the snippet, on the same principle that a job with no description at all is left
    alone. A guess dressed as a score is worse than an honest gap.
    """
    url = job.get("source_url") or job.get("apply_url")
    if not url:
        return None

    try:
        r = httpx.get(url, headers=_HEADERS, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
    except Exception as e:
        log.warning("[enrich] %s: could not fetch full description: %s",
                    job.get("title", "?"), str(e)[:120])
        return None

    text = _extract(r.text)
    if len(text) < _MIN_ENRICHED_CHARS:
        # Too short to be the posting — a wall, a stub, an expired-job page. Not an error
        # worth shouting about; just nothing usable.
        return None
    return text


def enrich_if_needed(job: dict) -> bool:
    """Replace an Adzuna snippet with the full posting, in place.

    Returns True if the description was replaced. Leaves the job untouched (and returns
    False) when it is not an Adzuna job, when the snippet is already long enough to be the
    real thing, or when the fetch came back with nothing usable — in that last case the
    caller sees a still-short description and, by the no-description rule, skips scoring it.
    """
    if not is_enrichable(job):
        return False

    # If Adzuna happened to return something already substantial, don't spend a request.
    if len((job.get("description") or "").strip()) >= _MIN_ENRICHED_CHARS:
        return False

    full = full_description(job)
    if not full or len(full) < MIN_DESCRIPTION_CHARS:
        return False

    job["description"] = full
    return True
