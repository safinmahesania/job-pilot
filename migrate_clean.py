import sqlite3
from src.normalize import clean_html

conn = sqlite3.connect("jobpilot.db")
rows = conn.execute("SELECT id, description FROM jobs").fetchall()
n = 0
for job_id, desc in rows:
    cleaned = clean_html(desc)
    if cleaned != desc:
        conn.execute("UPDATE jobs SET description = ? WHERE id = ?", (cleaned, job_id))
        n += 1
conn.commit()
conn.close()
print(f"Cleaned {n} descriptions")