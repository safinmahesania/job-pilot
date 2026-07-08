import httpx, re
from bs4 import BeautifulSoup

url = ("https://jobs.scotiabank.com/search-jobs/results?Keywords=developer"
       "&CurrentPage=1&RecordsPerPage=15&SearchType=5")
r = httpx.get(url, headers={"User-Agent":"Mozilla/5.0 (JobPilot)"}, timeout=25, follow_redirects=True)
soup = BeautifulSoup(r.text, "html.parser")

# common job-list containers try karo
for sel in ["li.job-listing", "ul#search-results-list li", ".job-listing",
            "section#search-results-list li", "a[data-job-id]", ".jobTitle"]:
    found = soup.select(sel)
    if found:
        print(f"MATCH '{sel}': {len(found)} elements")
        el = found[0]
        print("  sample:", el.get_text(strip=True)[:80])
        a = el.find("a") or (el if el.name=="a" else None)
        if a: print("  link:", a.get("href"))
        break
else:
    print("Koi known selector match nahi. Page me 'search-results' ke aas-paas dekho:")
    # links jinme /job/ ho
    links = [a for a in soup.find_all("a", href=True) if "/job" in a["href"].lower()]
    print(f"  /job links: {len(links)}")
    for a in links[:3]:
        print("   -", a.get_text(strip=True)[:50], "|", a["href"][:60])