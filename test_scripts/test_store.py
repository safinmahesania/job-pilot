import yaml
from src.adapters.base import get_adapter
from src.normalize import normalize
from src import store

conn = store.connect()

with open("../companies.yaml", encoding="utf-8") as f:
    companies = yaml.safe_load(f)["companies"]

new, skipped = 0, 0
for c in companies:
    if not c.get("active") or c.get("ats") != "greenhouse":
        continue
    for raw in get_adapter(c).fetch():
        job = normalize(raw)
        if store.already_seen(conn, job["dedupe_hash"]):
            skipped += 1
            continue
        # no scoring yet — store everything, mark kept
        job.update(score=None, skills_score=None, seniority_score=None,
                   domain_score=None, rationale=None, flags=None)
        store.save_job(conn, job)
        store.mark_seen(conn, job["dedupe_hash"], "kept")
        new += 1

conn.commit()
conn.close()
print(f"New: {new}, Skipped (already seen): {skipped}")