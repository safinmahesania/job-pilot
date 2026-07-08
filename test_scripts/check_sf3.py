import httpx
from bs4 import BeautifulSoup

url = ("https://jobs.scotiabank.com/search-jobs/results?Keywords=developer"
       "&CurrentPage=1&RecordsPerPage=15&SearchType=5")
r = httpx.get(url, headers={"User-Agent":"Mozilla/5.0 (JobPilot)"}, timeout=25, follow_redirects=True)
soup = BeautifulSoup(r.text, "html.parser")

# actual job links me aksar /job/ (singular id) hota hai, sort/search nahi
job_links = []
for a in soup.find_all("a", href=True):
    href = a["href"]
    txt = a.get_text(strip=True)
    # job detail links: /job/ ya numeric id, aur text me actual title (sort/filter nahi)
    if "/job/" in href.lower() and txt and "sort" not in href.lower():
        job_links.append((txt, href))

print(f"Job-detail links: {len(job_links)}")
for txt, href in job_links[:6]:
    print(f"  - {txt[:50]} | {href[:70]}")

# agar upar khaali, to search-results-list container ka structure dekho
if not job_links:
    container = soup.select_one("#search-results-list") or soup.select_one("section.results")
    if container:
        print("\nContainer mila. Uske andar ke pehle 2 li/a:")
        for el in container.find_all(["li","a"])[:6]:
            print("  ", el.name, "|", el.get_text(strip=True)[:40], "|", el.get("href","")[:50])
    else:
        print("\nNo container. Page JS-rendered ho sakta hai.")