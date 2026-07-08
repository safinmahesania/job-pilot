import yaml

data = yaml.safe_load(open("../companies.yaml", encoding="utf-8"))["companies"]
keys = ("query", "category", "level", "location", "max_pages", "limit", "feed")

print("=== ACTIVE SOURCES ===\n")
for c in data:
    if not c.get("active"):
        continue
    filters = {k: c[k] for k in keys if k in c}
    print(f"{c.get('ats',''):16} | {c['name']}")
    if filters:
        print(f"{'':16} | filters: {filters}")
    else:
        print(f"{'':16} | filters: none (fetches all jobs)")
    print()