import yaml
from src.adapters.base import get_adapter

with open("../companies.yaml", encoding="utf-8") as f:
    companies = yaml.safe_load(f)["companies"]

for c in companies:
    if not c.get("active") or c.get("ats") not in ("greenhouse", "lever"):
        continue
    try:
        jobs = get_adapter(c).fetch()
        print(f"\n{c['name']}: {len(jobs)} jobs")
        for j in jobs[:3]:
            print(f"  - {j['title']}  |  {j['apply_url']}")
    except Exception as e:
        print(f"\n{c['name']}: FAILED — {e}")