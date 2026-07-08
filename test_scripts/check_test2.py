import httpx

HOST = "jpmc.fa.oraclecloud.com"; SITE = "CX_1001"
url = (f"https://{HOST}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
       f"?onlyData=true&expand=requisitionList.secondaryLocations,flexFieldsFacet.values"
       f"&finder=findReqs;siteNumber={SITE},limit=1,"
       f"facetsList=LOCATIONS%3BWORK_LOCATIONS%3BTITLES%3BCATEGORIES")

r = httpx.get(url, headers={"Accept":"application/json","User-Agent":"Mozilla/5.0"}, timeout=30)
data = r.json()
facets = data.get("items", [{}])[0].get("secondaryLocations", None)
# facets alag structure me aate hain — poora dekh lete hain
import json
d = data.get("items", [{}])[0]
for k in d:
    if "acet" in k.lower() or "ocation" in k.lower():
        print(k, "=>", json.dumps(d[k])[:500])