"""Triage broken sources and apply the safe, obvious fixes automatically.

Health can tell you a source is broken but not what to do about it. This groups the
broken ones by the *kind* of failure and applies the fix that's safe to automate:

  * external outage (SSL handshake fail, read timeout, connection refused) — the site,
    not your config, is the problem. Disabled (set active: false) so it stops failing
    every run; re-enable if the site recovers.
  * rate limited (HTTP 429) — the API is throttling you (usually a used-up free tier).
    Disabled; needs a new key or a paid plan, which this script can't supply.
  * gone / not found (HTTP 404 / 410) — the slug is wrong or the board was removed.
    Left ALONE by default (could be a typo worth fixing) unless --delete-404 is passed.

"Never returned a job" with no error (a stub adapter or a wrong-but-valid slug) is NOT
touched — that needs a human to check the slug, and disabling it would hide the problem.

Dry-run by default: prints what it WOULD do. Pass --apply to write changes.

    python -m scripts.fix_broken_sources               # show the plan
    python -m scripts.fix_broken_sources --apply        # disable external/rate-limited
    python -m scripts.fix_broken_sources --apply --delete-404   # also delete 404 slugs
"""
import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src import configio                                  # noqa: E402
from src.store import connect                              # noqa: E402
from src import health                                    # noqa: E402
from src.paths import COMPANIES_FILE                      # noqa: E402

# Match a failure kind from the health detail/error text.
EXTERNAL = re.compile(r"ssl|handshake|timed out|timeout|connection|refused|"
                      r"getaddrinfo|temporarily unavailable|read operation", re.I)
RATE_LIMIT = re.compile(r"429|too many requests|rate.?limit", re.I)
GONE = re.compile(r"404|not found|410|gone", re.I)


def _classify(detail: str) -> str:
    d = detail or ""
    if RATE_LIMIT.search(d):
        return "rate_limited"
    if GONE.search(d):
        return "gone"
    if EXTERNAL.search(d):
        return "external"
    return "other"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--delete-404", action="store_true",
                    help="also delete sources whose slug 404s")
    args = ap.parse_args()

    conn = connect()
    broken = health.broken(conn)          # verdict in erroring/silent/never_worked
    # Only ones with an error string tell us the kind; "never_worked" without an error
    # stays untouched (needs a human to check the slug).
    plans = {"external": [], "rate_limited": [], "gone": [], "other": []}
    for b in broken:
        kind = _classify(b.get("detail") or b.get("error") or "")
        plans[kind].append(b["name"])

    data = configio.read_yaml(COMPANIES_FILE) or {"companies": []}
    companies = data.get("companies", [])
    by_name = {c.get("name"): c for c in companies}

    to_disable = plans["external"] + plans["rate_limited"]
    to_delete = plans["gone"] if args.delete_404 else []

    print("Plan:")
    print(f"  Disable (external outage / rate limited): {to_disable or '—'}")
    print(f"  Delete (404 slug){' ' if args.delete_404 else ' [skipped; use --delete-404] '}: "
          f"{plans['gone'] or '—'}")
    print(f"  Leave alone (needs a human — check the slug): "
          f"{plans['other'] or '—'}")

    if not args.apply:
        print("\nDry run — nothing changed. Re-run with --apply to make these changes.")
        return 0

    changed = 0
    for name in to_disable:
        c = by_name.get(name)
        if c and c.get("active"):
            c["active"] = False
            changed += 1
    if to_delete:
        keep = [c for c in companies if c.get("name") not in to_delete]
        data["companies"] = keep
        # also clear their health so they stop showing
        for name in to_delete:
            conn.execute("DELETE FROM source_health WHERE name = ?", (name,))
        conn.commit()
        changed += len(companies) - len(keep)

    configio.write_yaml(COMPANIES_FILE, data)
    print(f"\nApplied. {changed} source(s) changed. "
          f"Disabled sources moved to the bottom of the list.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
