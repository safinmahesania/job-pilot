"""Generic HTML careers-page scraper.

Some boards have no JSON API — SAP SuccessFactors tenants (National Bank, Deloitte),
job aggregators (Talent.com, Eluta), and bespoke company career pages (CGI, Intact,
ITjobs). They render their listings as HTML, so there is no clean feed to read; the only
way in is to fetch the page and pull the job links out of it.

This one adapter serves all three `ats` values (`custom`, `aggregator`, `successfactors`)
because for our purposes the job is the same: given a careers URL, find the anchors that
look like individual job postings and return a title + link for each. It is deliberately
best-effort — it will not match every site's markup perfectly, and it returns titles and
links rather than full descriptions (the pipeline can follow the link later). What it
buys is that these sources produce *something* and fail *loudly* in the error log when a
page changes, instead of being a dead entry that silently returns nothing.

HTML scraping is fragile by nature: when a site is redesigned, its selectors rot and this
adapter for that site goes quiet. That is expected — the source-health view is what makes
it visible, and a selector can be nudged in `_looks_like_job` without touching anything
else.
"""
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from src.adapters.base import SourceAdapter
from src.logs import log

_HEADERS = {"User-Agent": "Mozilla/5.0 (JobPilot; +https://github.com/safinmahesania/job-pilot)"}

# Anchors whose href contains one of these path fragments are very likely a single job
# posting rather than a nav link, a category, or a login. Kept broad on purpose: a false
# positive is cheap (it gets a low score or is dismissed), a false negative means a real
# job is never seen.
_JOB_PATH_HINTS = (
    "/job/", "/jobs/", "/job-", "/careers/", "/career/", "/opening",
    "/position", "/vacancy", "/vacancies", "/posting", "/apply/", "/req",
    "/en-ca/careers", "/emploi", "/offre",
)

# Anchors we never want — navigation, filters, auth, social, and the careers landing
# itself. Checked against the whole href, lowercased.
_SKIP_HINTS = (
    "login", "signin", "sign-in", "register", "privacy", "cookie", "terms",
    "facebook", "twitter", "linkedin.com", "instagram", "youtube",
    "mailto:", "tel:", "javascript:", "#", "?sort", "?page=", "/search?",
    "/category", "/categories", "/location", "/departments",
)


class GenericCareersAdapter(SourceAdapter):
    """Scrape a careers URL for anything that looks like an individual job link."""

    def fetch(self) -> list[dict]:
        c = self.company
        careers_url = c.get("careers_url") or c.get("base") or c.get("url")
        if not careers_url:
            log.warning("[generic %s] no careers_url configured", self.name)
            return []

        ats = c.get("ats", "custom")
        max_pages = int(c.get("max_pages", 1))
        query = c.get("query", "")

        seen: set[str] = set()
        out: list[dict] = []

        for page in range(1, max_pages + 1):
            url = self._page_url(careers_url, page, query)
            try:
                r = httpx.get(url, headers=_HEADERS, timeout=25, follow_redirects=True)
                r.raise_for_status()
            except Exception as e:
                log.warning("[%s %s] page %s fetch failed: %s", ats, self.name, page, e)
                break

            soup = BeautifulSoup(r.text, "html.parser")
            base_host = urlparse(str(r.url)).netloc

            page_hits = 0
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                title = a.get_text(strip=True)
                if not self._looks_like_job(href, title):
                    continue

                job_url = urljoin(str(r.url), href)
                # Only keep links that stay on the careers host — off-site links are
                # almost always ads, partners, or social, not this company's jobs.
                if urlparse(job_url).netloc and urlparse(job_url).netloc != base_host:
                    # aggregators legitimately link off-site; allow it for them
                    if ats != "aggregator":
                        continue

                if job_url in seen:
                    continue
                seen.add(job_url)
                page_hits += 1

                out.append({
                    "source": f"{ats}:{self.name.lower().replace(' ', '')}",
                    "company": self.name,
                    "title": title,
                    "location": self._guess_location(a),
                    "source_url": job_url,
                    "apply_url": job_url,
                    "description": "",       # follow the link later for the body
                    "posted_date": "",
                    "job_type": None,
                })

            if page_hits == 0:
                break                        # nothing on this page -> stop paging

        if not out:
            log.info("[%s %s] no job links matched at %s", ats, self.name, careers_url)
        return out

    # ── helpers ──

    @staticmethod
    def _looks_like_job(href: str, title: str) -> bool:
        if not title or len(title) < 3:
            return False
        low = href.lower()
        if any(skip in low for skip in _SKIP_HINTS):
            return False
        return any(hint in low for hint in _JOB_PATH_HINTS)

    @staticmethod
    def _guess_location(anchor) -> str:
        """Best-effort: a nearby element whose class mentions 'location'."""
        parent = anchor.find_parent(["li", "div", "article", "tr"])
        if not parent:
            return ""
        node = parent.find(class_=lambda x: x and "location" in x.lower())
        if node:
            return node.get_text(strip=True)
        # some pages encode the city in the href: /job/Toronto-Developer-123
        m = re.search(r"/job/([A-Za-z]{3,})[-/]", anchor.get("href", ""))
        return m.group(1) if m else ""

    @staticmethod
    def _page_url(base: str, page: int, query: str) -> str:
        """Add a page parameter for multi-page careers pages. Page 1 is the URL as-is."""
        if page == 1:
            return base
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}page={page}"
