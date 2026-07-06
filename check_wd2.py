import httpx
from src.config import load_companies
from src.adapters.base import get_adapter

td = [c for c in load_companies() if c.get("ats") == "workday" and "TD" in c["name"]][0]
jobs = get_adapter(td).fetch()

# raw posting se externalPath nikaalne ke liye adapter ke andar ki call dobara karte hain
tenant, host, site = td["tenant"], td.get("host","wd3"), td["site"]
base = f"https://{tenant}.{host}.myworkdayjobs.com"
api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
r = httpx.post(api, json={"appliedFacets":{}, "limit":20, "offset":0, "searchText":""},
               headers={"Content-Type":"application/json","Accept":"application/json"}, timeout=25)
postings = r.json().get("jobPostings", [])
p = next((x for x in postings if "JSP" in (x.get("title") or "")), postings[0])
path = p.get("externalPath")
print("PATH:", path)

# detail endpoint hit
durl = f"{base}/wday/cxs/{tenant}/{site}{path}"
print("URL:", durl)
dr = httpx.get(durl, headers={"Accept":"application/json"}, timeout=25)
print("STATUS:", dr.status_code)
info = dr.json().get("jobPostingInfo", {})
print("KEYS:", list(info.keys()))
print("DESC LEN:", len(info.get("jobDescription") or ""))