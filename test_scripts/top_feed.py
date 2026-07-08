import sqlite3

conn = sqlite3.connect("jobpilot.db")
rows = conn.execute(
    "SELECT score, company, title FROM jobs "
    "WHERE status = 'surfaced' ORDER BY score DESC LIMIT 20"
).fetchall()
conn.close()

for score, company, title in rows:
    print(f"{score:5.0f} | {company[:18]:18} | {title[:42]}")