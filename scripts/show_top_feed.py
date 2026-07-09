"""Print the current top-scored surfaced jobs. Run from the project root:

    python scripts/show_top_feed.py
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import sqlite3
from src.paths import DB_PATH

conn = sqlite3.connect(DB_PATH)
rows = conn.execute(
    "SELECT score, company, title FROM jobs "
    "WHERE status = 'surfaced' ORDER BY score DESC LIMIT 20"
).fetchall()
conn.close()

for score, company, title in rows:
    print(f"{score:5.0f} | {company[:18]:18} | {title[:42]}")