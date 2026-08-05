"""Bulk-add company boards to companies-backup.yaml from a CSV.

The CSV must have at least ``Name``, ``ATS`` and ``Identifier`` columns (extra columns
like CareerPageURL / Headquarters / WorkMode are ignored — they're for your own notes).
ATS values are matched case-insensitively against the adapters JobPilot knows.

Greenhouse / Lever / Ashby / Workable / SmartRecruiters need only an identifier, so they
import ready to fetch. Workday needs tenant + host + site (from the careers URL:
https://<tenant>.<host>.myworkdayjobs.com/<site>) which a Name/Identifier CSV doesn't
carry — those rows are added as INACTIVE with the identifier guessed as the tenant and
host/site left blank for you to fill in, so nothing runs half-configured.

Existing entries (same ats + identifier/tenant, case-insensitive) are skipped, so re-running
is safe.

Usage:
    python scripts/import_sources_csv.py path/to/sources.csv
    python scripts/import_sources_csv.py path/to/sources.csv --active   # start them enabled
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import configio                         # noqa: E402
from src.adapters.base import KNOWN_ATS          # noqa: E402
from src.paths import COMPANIES_FILE             # noqa: E402

# ATS that fetch from just an identifier token.
IDENTIFIER_ATS = {"greenhouse", "lever", "ashby", "workable", "smartrecruiters"}
# ATS that need more than an identifier — we can't fully configure these from a simple CSV.
NEEDS_DETAIL = {"workday", "oracle", "phenom", "successfactors", "custom"}


def _key(entry: dict) -> tuple:
    """Identity of a board for dedupe: ats + whichever locator it uses."""
    locator = (entry.get("identifier") or entry.get("tenant")
               or entry.get("host") or entry.get("base") or "").lower()
    return (entry.get("ats", "").lower(), locator)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", help="CSV with Name, ATS, Identifier columns")
    ap.add_argument("--active", action="store_true",
                    help="add ready boards as active (default: active for identifier ATS, "
                         "inactive for ones needing more detail)")
    args = ap.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        print(f"CSV not found: {csv_path}", file=sys.stderr)
        return 1

    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    if not rows:
        print("CSV has no rows.", file=sys.stderr)
        return 1

    # Column names are matched loosely so "ATS"/"ats" and "Identifier"/"identifier" work.
    def col(row: dict, *names: str) -> str:
        for n in names:
            for k, v in row.items():
                if k and k.strip().lower() == n:
                    return (v or "").strip()
        return ""

    data = configio.read_yaml(COMPANIES_FILE) or {"companies": []}
    data.setdefault("companies", [])
    existing = {_key(c) for c in data["companies"]}

    added, skipped, needs_detail, unknown = [], [], [], []
    for row in rows:
        name = col(row, "name")
        ats = col(row, "ats").lower()
        ident = col(row, "identifier", "token")
        if not name or not ats:
            continue
        if ats not in KNOWN_ATS:
            unknown.append(f"{name} ({ats})")
            continue

        entry: dict = {"name": name, "ats": ats}
        if ats in IDENTIFIER_ATS:
            entry["identifier"] = ident
            entry["active"] = bool(args.active) or True   # ready to run
        elif ats in NEEDS_DETAIL:
            # Best-effort: treat the identifier as the tenant, leave host/site blank,
            # keep it INACTIVE so a half-configured board never runs.
            entry["tenant"] = ident
            entry["host"] = ""
            entry["site"] = ""
            entry["active"] = False
            needs_detail.append(name)
        else:
            entry["identifier"] = ident
            entry["active"] = bool(args.active)

        if _key(entry) in existing:
            skipped.append(name)
            continue
        data["companies"].append(entry)
        existing.add(_key(entry))
        added.append(name)

    configio.write_yaml(COMPANIES_FILE, data)

    print(f"Added {len(added)} board(s); {len(skipped)} already present.")
    if needs_detail:
        print(f"\n{len(needs_detail)} Workday/Oracle board(s) added as INACTIVE — they need "
              f"tenant/host/site filled in before they'll fetch:")
        for n in needs_detail:
            print(f"  - {n}")
    if unknown:
        print(f"\nSkipped {len(unknown)} row(s) with an unknown ATS:")
        for n in unknown:
            print(f"  - {n}")
    print(f"\nTotal boards now: {len(data['companies'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
