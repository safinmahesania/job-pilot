"""Create (or update) the SQLite database from schema.sql.

Idempotent and safe to re-run: every CREATE uses IF NOT EXISTS, so existing data
is never touched.

The subtle part is columns. `CREATE TABLE IF NOT EXISTS` does nothing at all if
the table already exists — including when schema.sql has since grown a column. So
after running the schema we compare it against the live database and ADD COLUMN
for anything missing. Without this, a schema change silently does nothing on every
machine that already has a database, which is the worst kind of bug: it works
perfectly on a fresh clone and fails only for people with real data.

    python data/init_db.py
"""
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.paths import DB_PATH, SCHEMA_PATH


def _declared_columns(schema: str) -> dict[str, list[tuple[str, str]]]:
    """{table: [(column, full definition), ...]} as declared in schema.sql."""
    tables = {}
    for match in re.finditer(
        r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\n\);", schema, re.S | re.I
    ):
        table, body = match.group(1), match.group(2)
        columns = []
        for line in body.splitlines():
            line = line.split("--")[0].strip().rstrip(",")
            if not line:
                continue
            # Skip table-level constraints; we only migrate plain columns.
            if re.match(r"^(PRIMARY|FOREIGN|UNIQUE|CHECK|CONSTRAINT)\b", line, re.I):
                continue
            name = line.split()[0]
            if name.isidentifier():
                columns.append((name, line))
        tables[table] = columns
    return tables


def _add_missing_columns(conn, schema: str) -> list[str]:
    """ALTER TABLE for every column schema.sql declares that the DB lacks."""
    added = []
    for table, columns in _declared_columns(schema).items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue                     # table doesn't exist; the CREATE made it
        for name, definition in columns:
            if name in existing:
                continue
            # SQLite can't ADD COLUMN with a non-constant default or PRIMARY KEY.
            # Our added columns are plain and nullable, which is all we need.
            safe = re.sub(r"\bPRIMARY KEY\b|\bAUTOINCREMENT\b|\bUNIQUE\b", "",
                          definition, flags=re.I).strip()
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {safe}")
                added.append(f"{table}.{name}")
            except sqlite3.OperationalError as e:
                print(f"  could not add {table}.{name}: {e}")
    return added


def main():
    schema = Path(SCHEMA_PATH).read_text(encoding="utf-8")

    conn = sqlite3.connect(DB_PATH)

    # Order matters. Running the whole schema first looks obvious and is wrong:
    # an index or view further down may reference a column the live table doesn't
    # have yet, which raises and leaves the migration unrun. So: create the tables,
    # bring their columns up to date, and only then run everything else.
    creates = re.findall(r"CREATE TABLE IF NOT EXISTS.*?\n\);", schema, re.S | re.I)
    for statement in creates:
        conn.execute(statement)

    added = _add_missing_columns(conn, schema)

    conn.executescript(schema)               # indexes, triggers, seed rows
    conn.commit()
    conn.close()

    print(f"Database ready: {DB_PATH}")
    if added:
        print("Added missing columns: " + ", ".join(added))


if __name__ == "__main__":
    main()
