"""Create (or update) the SQLite database from schema.sql.

Idempotent: every CREATE in schema.sql uses IF NOT EXISTS, so re-running this is
safe and will not wipe existing data. Run it once after cloning:

    python data/init_db.py
"""
import sqlite3

# Import the canonical paths. This works whether the script is run from the
# project root (`python data/init_db.py`) or as a module.
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.paths import DB_PATH, SCHEMA_PATH


def main():
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = f.read()

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(schema)
    conn.commit()
    conn.close()
    print(f"Database ready: {DB_PATH}")


if __name__ == "__main__":
    main()
