"""Verify extraction quality — dumps N random extracted jobs with their original
description next to the 11 extracted fields, so you can eyeball correctness.

Usage (from project root):
    python verify_extraction.py            # 12 random jobs, prints to console
    python verify_extraction.py 15         # 15 random jobs
    python verify_extraction.py 15 report  # also writes extraction_report.txt to share

Reads data/jobpilot.db directly. Read-only — never writes to the DB.
"""

from __future__ import annotations

import sqlite3
import sys
import textwrap
from pathlib import Path

# 11 extracted fields, in a sensible reading order
FIELDS = [
    "work_mode",
    "seniority_level",
    "location_detail",
    "salary_text",
    "benefits",
    "responsibilities",
    "requirements",
    "nice_to_have",
    "tech_stack",
    "about_company",
    "instructions",
]

DB_PATH = Path(__file__).parent / "data" / "jobpilot.db"
DESC_LIMIT = 1800  # how much of the raw description to show (chars)


def _wrap(label: str, value: str, width: int = 100) -> str:
    """Indented, wrapped field for readability."""
    value = (value or "").strip()
    if not value:
        return f"  {label:<16} (blank)"
    lines = textwrap.wrap(value, width=width) or [value]
    out = f"  {label:<16} {lines[0]}"
    for ln in lines[1:]:
        out += f"\n  {'':<16} {ln}"
    return out


def dump(n: int, write_report: bool) -> None:
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}. Run this from the project root.")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Only jobs that were actually extracted (extracted_at set)
    cols = "id, title, company, location, description, extracted_at, " + ", ".join(FIELDS)
    rows = conn.execute(
        f"SELECT {cols} FROM jobs "
        f"WHERE extracted_at IS NOT NULL AND description IS NOT NULL "
        f"ORDER BY RANDOM() LIMIT ?",
        (n,),
    ).fetchall()

    total = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE extracted_at IS NOT NULL"
    ).fetchone()[0]
    grand = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()

    if not rows:
        print("No extracted jobs found. Did the backfill run?")
        return

    out_lines: list[str] = []

    def emit(s: str = "") -> None:
        print(s)
        out_lines.append(s)

    emit("=" * 104)
    emit(f"EXTRACTION VERIFICATION — {len(rows)} random jobs "
         f"({total} of {grand} jobs extracted)")
    emit("=" * 104)

    for i, r in enumerate(rows, 1):
        emit("")
        emit("#" * 104)
        emit(f"[{i}/{len(rows)}]  JOB {r['id']}  —  {r['title']}  @  {r['company']}")
        emit(f"         raw location: {r['location'] or '(none)'}")
        emit("#" * 104)
        emit("")
        emit("── ORIGINAL DESCRIPTION " + "─" * 80)
        desc = (r["description"] or "").strip()
        shown = desc[:DESC_LIMIT]
        for para in shown.splitlines():
            for ln in textwrap.wrap(para, width=100) or [""]:
                emit("  " + ln)
        if len(desc) > DESC_LIMIT:
            emit(f"  … [{len(desc) - DESC_LIMIT} more chars truncated]")
        emit("")
        emit("── EXTRACTED FIELDS " + "─" * 84)
        for f in FIELDS:
            emit(_wrap(f, r[f]))
        emit("")

    emit("=" * 104)
    emit("END. Check each field against the description above it: correct? honest "
         "(blank when the posting is silent, not guessed)?")
    emit("=" * 104)

    if write_report:
        report = Path(__file__).parent / "extraction_report.txt"
        report.write_text("\n".join(out_lines), encoding="utf-8")
        print(f"\n>>> Report written to {report}  — share this file.")


if __name__ == "__main__":
    n = 12
    write_report = False
    for arg in sys.argv[1:]:
        if arg.isdigit():
            n = int(arg)
        elif arg.lower() in ("report", "-r", "--report"):
            write_report = True
    dump(n, write_report)
