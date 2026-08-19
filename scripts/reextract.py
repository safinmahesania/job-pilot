"""Re-run extraction over EXISTING jobs with the current (improved) prompt.

Normal extraction skips any job whose extracted_at is already set, so a prompt
improvement never reaches jobs that were extracted under the old prompt. This
script re-extracts them in place — one LLM call per job, updating the 11 fields
and re-stamping extracted_at. Future jobs already use the new prompt via the fetch
pipeline; this is a one-time cleanup of the jobs already in the database, meant to
be run before migrating.

Usage (from project root or scripts/):
    python scripts/reextract.py --feed              # only feed jobs (surfaced & above threshold) — test the new prompt first
    python scripts/reextract.py --feed --limit 20   # just 20 feed jobs
    python scripts/reextract.py --all               # every job with a real description
    python scripts/reextract.py --all --limit 100   # first 100 of them

Reads data/jobpilot.db and uses your configured LLM provider chain (same as the
app), so run it in the environment where the server runs (keys in .env available).
Only the 11 extraction columns + extracted_at are written — titles, scores and
statuses are never touched.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path


def _project_root() -> Path:
    """Find the folder that holds both src/ and data/, from anywhere."""
    starts = [Path(__file__).resolve().parent, Path.cwd()]
    for start in starts:
        for base in [start, *start.parents]:
            if (base / "src" / "extract.py").exists() and (base / "data").exists():
                return base
    print("Could not locate the project root (needs src/ and data/).")
    sys.exit(1)


ROOT = _project_root()
sys.path.insert(0, str(ROOT))  # so `from src import ...` works when run from scripts/

from src import extract, store  # noqa: E402

DB_PATH = ROOT / "data" / "jobpilot.db"


def _select(conn: sqlite3.Connection, feed_only: bool, limit: int | None):
    where = (
        "description IS NOT NULL AND length(trim(description)) >= ? "
    )
    params: list = [extract.MIN_DESCRIPTION_CHARS]
    if feed_only:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='score_threshold'"
        ).fetchone()
        threshold = int(row[0]) if row and str(row[0]).strip().isdigit() else 70
        where += "AND status = 'surfaced' AND score >= ? "
        params.append(threshold)
    sql = (
        "SELECT id, title, company, location, description FROM jobs "
        f"WHERE {where} ORDER BY id"
    )
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def main() -> None:
    feed_only = "--feed" in sys.argv
    all_jobs = "--all" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        i = sys.argv.index("--limit")
        if i + 1 < len(sys.argv) and sys.argv[i + 1].isdigit():
            limit = int(sys.argv[i + 1])

    if not feed_only and not all_jobs:
        print("Pick a scope: --feed (recommended first) or --all. "
              "Optional: --limit N.")
        sys.exit(1)

    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}.")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    rows = _select(conn, feed_only, limit)
    scope = "feed" if feed_only else "all"
    total = len(rows)
    if total == 0:
        print(f"No jobs matched (scope={scope}).")
        return

    print(f"Re-extracting {total} jobs (scope={scope}) with the current prompt.")
    print("One LLM call each — this will take a while. Ctrl+C to stop; progress is "
          "saved per job.\n")

    done = skipped = failed = 0
    start = time.time()
    for n, row in enumerate(rows, 1):
        job = {"title": row[1], "company": row[2], "location": row[3],
               "description": row[4]}
        try:
            ex = extract.extract(job)
        except Exception as e:  # noqa: BLE001 — keep going, log to console
            failed += 1
            print(f"  [{n}/{total}] job {row[0]} FAILED: {e}")
            continue
        if ex is None:
            skipped += 1
            continue
        store.update_extraction(conn, row[0], ex.model_dump())
        done += 1
        if n % 10 == 0 or n == total:
            rate = n / max(1e-6, time.time() - start)
            eta = (total - n) / max(1e-6, rate)
            print(f"  [{n}/{total}]  done={done} skipped={skipped} failed={failed}"
                  f"   ~{eta/60:.1f} min left")

    conn.close()
    print(f"\nFinished. Re-extracted {done}, nothing-to-read {skipped}, "
          f"failed {failed}, of {total}.")
    print("Run verify_extraction.py to spot-check the results.")


if __name__ == "__main__":
    main()
