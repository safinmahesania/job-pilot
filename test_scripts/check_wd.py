from src.config import load_companies
from src.adapters.base import get_adapter

td = [c for c in load_companies() if c.get("ats") == "workday" and "TD" in c["name"]][0]
jobs = get_adapter(td).fetch()
j = next((x for x in jobs if "JSP" in (x.get("title") or "")), jobs[0])
print("TITLE:", j.get("title"))
print("JOB_TYPE:", j.get("job_type"))
print("DESC LEN:", len(j.get("description") or ""))
print("DESC START:", (j.get("description") or "")[:200])