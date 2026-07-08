import httpx

# Scotiabank SuccessFactors — common career-search API pattern
# SF sites aksar: https://{company}.jobs.sap.com ya careers site with /services/careersection
urls = [
    "https://jobs.scotiabank.com/search-jobs/results?ActiveFacetID=0&CurrentPage=1&RecordsPerPage=15&Distance=50&RadiusUnitType=0&Keywords=developer&Location=&ShowRadius=False&IsPagination=False&CustomFacetName=&FacetTerm=&FacetType=0&SearchResultsModuleName=Search+Results&SearchFiltersModuleName=Search+Filters&SortCriteria=0&SortDirection=0&SearchType=5",
]
for url in urls:
    try:
        r = httpx.get(url, headers={"Accept":"application/json, text/html","User-Agent":"Mozilla/5.0 (JobPilot)"}, timeout=25, follow_redirects=True)
        print("URL:", url[:60])
        print("STATUS:", r.status_code, "| TYPE:", r.headers.get("content-type","")[:40], "| LEN:", len(r.text))
        print("SNIPPET:", r.text[:200].replace("\n"," "))
        print("---")
    except Exception as e:
        print("FAILED:", e)