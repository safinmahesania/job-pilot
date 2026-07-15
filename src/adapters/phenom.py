"""Phenom People careers adapter (e.g. Scotiabank) — HTML scrape."""
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from .base import SourceAdapter
from src.logs import log


class PhenomAdapter(SourceAdapter):
    def fetch(self) -> list[dict]:
        c = self.company
        base = c["base"].rstrip("/")        # e.g. https://jobs.scotiabank.com
        query = c.get("query", "")
        max_pages = c.get("max_pages", 2)
        headers = {"User-Agent": "Mozilla/5.0 (JobPilot)"}
        seen, out = set(), []

        for page in range(1, max_pages + 1):
            url = (f"{base}/search-jobs/results?q={query.replace(' ', '%20')}"
                   f"&CurrentPage={page}&RecordsPerPage=15&SearchType=5")
            try:
                r = httpx.get(url, headers=headers, timeout=25, follow_redirects=True)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "html.parser")
            except Exception as e:
                log.warning("[Phenom %s] page %s failed: %s", self.name, page, e)
                break

            links = [a for a in soup.find_all("a", href=True)
                     if "/job/" in a["href"].lower() and a.get_text(strip=True)
                     and "sort" not in a["href"].lower()]
            if not links:
                break

            for a in links:
                href = a["href"]
                if href in seen:
                    continue
                seen.add(href)
                title = a.get_text(strip=True)
                # location aksar title ke aas-paas span me; best-effort
                li = a.find_parent("li")
                loc = ""
                if li:
                    locspan = li.find(class_=lambda x: x and "location" in x.lower())
                    if locspan:
                        loc = locspan.get_text(strip=True)

                        # href/title se location guess (Phenom href me city hota hai)
                if not loc:
                    import re
                    m = re.search(r'/job/([A-Za-z]+)-', href)
                    if m:
                        loc = m.group(1)

                job_url = urljoin(base, href)
                out.append({
                    "source": "phenom:" + self.name.lower().replace(" ", ""),
                    "company": self.name,
                    "title": title,
                    "location": loc,
                    "source_url": job_url,
                    "apply_url": job_url,
                    "description": "",     # detail page se baad me, abhi title-based
                    "posted_date": "",
                    "job_type": None,
                })
        return out