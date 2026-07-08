import httpx, json

HOST = "jpmc.fa.oraclecloud.com"
SITE = "CX_1001"
url = (f"https://{HOST}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
       f"?onlyData=true"
       f"&expand=requisitionList.secondaryLocations,flexFieldsFacet.values"
       f"&finder=findReqs;siteNumber={SITE},limit=5")

r = httpx.get(url, headers={"Accept": "application/json",
                            "User-Agent": "Mozilla/5.0 (JobPilot)"}, timeout=30)
print("STATUS:", r.status_code)
try:
    data = r.json()
    print("TOP KEYS:", list(data.keys()))
    items = data.get("items", [])
    print("ITEMS:", len(items))
    if items:
        reqs = items[0].get("requisitionList", [])
        print("REQS in first item:", len(reqs))
        if reqs:
            print("FIRST JOB KEYS:", list(reqs[0].keys()))
            j = reqs[0]
            print("  Title:", j.get("Title"))
            print("  Id:", j.get("Id"))
            print("  Location:", j.get("PrimaryLocation"))
            print("  Posted:", j.get("PostedDate"))
except Exception as e:
    print("Parse failed:", e)
    print(r.text[:300])