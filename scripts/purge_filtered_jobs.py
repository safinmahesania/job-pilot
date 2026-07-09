"""Re-run the prefilter over already-surfaced jobs and dismiss any that no
longer pass (e.g. after tightening profile.yaml). Run from the project root:

    python scripts/purge_filtered_jobs.py
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import sqlite3
from src.config import load_profile
from src.scoring.prefilter import passes
from src.paths import DB_PATH

profile = load_profile()
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

rows = conn.execute(
    "SELECT id, title, company, location, description, job_type, posted_date, source "
    "FROM jobs WHERE status = 'surfaced'"
).fetchall()

dismissed = []
for r in rows:
    job = dict(r)
    if not passes(job, profile):
        conn.execute("UPDATE jobs SET status='dismissed' WHERE id=?", (job["id"],))
        dismissed.append(f'{job["company"]} — {job["title"][:45]}')

conn.commit()
conn.close()

print(f"Dismissed {len(dismissed)}:")
for d in dismissed:
    print("  -", d)