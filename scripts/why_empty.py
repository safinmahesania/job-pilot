"""Why is the feed empty? — a live prefilter breakdown.

The pipeline throws dropped jobs away (it only stores the ones that survive), so the
database can't tell you *why* the feed is empty. This re-fetches your active sources and
runs the same prefilter over the fresh jobs, counting instead of discarding.

    python -m scripts.why_empty                  # all active sources
    python -m scripts.why_empty --limit 5        # first 5 sources (faster)
    python -m scripts.why_empty --limit 5 --show # also print example jobs per rule
"""
import argparse
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.modules.setdefault("ollama", types.ModuleType("ollama"))

from src import configio                     # noqa: E402
from src.adapters.base import get_adapter    # noqa: E402
from src.normalize import normalize, is_valid  # noqa: E402
from src.scoring import prefilter            # noqa: E402


def _classify(job, c, s):
    if not is_valid(job):
        return "invalid"
    if not prefilter._check_locations(job, c.get("locations")):
        return "location"
    if not prefilter._check_salary_floor(job, c.get("salary_floor")):
        return "salary"
    if not prefilter._check_sponsorship(job, c.get("needs_sponsorship")):
        return "sponsorship"
    if not prefilter._ok_level(job, s):
        return "level"
    if not prefilter._ok_domain(job, s):
        return "domain"
    if not prefilter._ok_job_type(job, s):
        return "job_type"
    if not prefilter._ok_recency(job, s):
        return "recency"
    if not prefilter._ok_exclude_keywords(job, s):
        return "exclude_keywords"
    return "passes_all"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="only test the first N active sources (0 = all)")
    ap.add_argument("--show", action="store_true",
                    help="print example jobs for each rule")
    args = ap.parse_args()

    profile = configio.read_yaml("profile.yaml") or {}
    if not profile:
        print("No profile.yaml found — nothing to filter against.")
        return

    companies = (configio.read_yaml("companies.yaml") or {}).get("companies", [])
    active = [c for c in companies if c.get("active")]
    if args.limit:
        active = active[: args.limit]
    if not active:
        print("No active sources configured.")
        return

    c = profile.get("constraints", {})
    s = profile.get("search", {})

    buckets = {k: [] for k in ("invalid", "location", "salary", "sponsorship", "level",
                               "domain", "job_type", "recency", "exclude_keywords",
                               "passes_all")}
    total = 0
    fetch_errors = []

    print(f"\n  Fetching {len(active)} source(s)…\n")
    for company in active:
        try:
            raw = get_adapter(company).fetch()
        except Exception as e:
            fetch_errors.append((company["name"], str(e)[:60]))
            continue
        for r in raw:
            try:
                job = normalize(r)
            except Exception:
                continue
            total += 1
            buckets[_classify(job, c, s)].append(job)

    if not total:
        print("  No jobs fetched — a source problem, not a filter one.")
        for name, err in fetch_errors:
            print(f"    {name}: {err}")
        return

    print(f"  {total} jobs fetched across {len(active)} source(s)\n")
    print("  Dropped by each rule (first failing rule wins):")
    for name, jobs in buckets.items():
        if name == "passes_all":
            continue
        n = len(jobs)
        pct = round(100 * n / total, 1)
        print(f"    {name:18} {n:5}  {pct:5}%  {'#' * int(pct / 3)}")
    passed = len(buckets["passes_all"])
    print(f"\n  Would reach scoring: {passed}  ({round(100 * passed / total, 1)}%)")

    if args.show:
        print("\n  ── Examples per rule (title | location) ──")
        for name, jobs in buckets.items():
            if not jobs:
                continue
            label = "PASSED" if name == "passes_all" else name
            print(f"\n  [{label}]")
            for j in jobs[:6]:
                print(f"    · {(j.get('title') or '')[:50]:50} | "
                      f"{(j.get('location') or '')[:30]}")

    if fetch_errors:
        print(f"\n  {len(fetch_errors)} source(s) failed to fetch:")
        for name, err in fetch_errors[:10]:
            print(f"    {name}: {err}")


if __name__ == "__main__":
    main()
