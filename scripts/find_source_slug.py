"""Find the correct ATS + slug for a source that returns nothing.

"The board answered and had nothing" almost always means the slug is wrong — the company
uses a different token, or a different ATS entirely. This probes the public board APIs of
Greenhouse, Lever, Ashby and Workable with a set of slug guesses derived from the name,
and reports which combination actually returns jobs.

    python -m scripts.find_source_slug Plusgrade Thinkific Jobspresso
    python -m scripts.find_source_slug "Deloitte Canada" --extra deloitte deloittecanada

For each name it tries: the name lowercased, with spaces/punctuation stripped, with "hq"
and "inc" removed, plus any --extra guesses you pass. The first ATS+slug that returns >0
jobs is your answer — put it in companies-backup.yaml.

Needs network access (run it on your own machine, not in a sandbox). Nothing is saved.
"""
import argparse
import re
import sys
from pathlib import Path

import httpx

HEADERS = {"User-Agent": "Mozilla/5.0 (JobPilot slug finder)",
           "Accept": "application/json"}
TIMEOUT = 12

# (ats, url template, function returning the job count from the parsed JSON)
PROBES = [
    ("greenhouse",
     "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
     lambda d: len(d.get("jobs", []))),
    ("lever",
     "https://api.lever.co/v0/postings/{slug}?mode=json",
     lambda d: len(d) if isinstance(d, list) else 0),
    ("ashby",
     "https://api.ashbyhq.com/posting-api/job-board/{slug}",
     lambda d: len(d.get("jobs", []))),
    ("workable",
     "https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
     lambda d: len(d.get("jobs", []))),
    ("smartrecruiters",
     "https://api.smartrecruiters.com/v1/companies/{slug}/postings",
     lambda d: d.get("totalFound", 0) if isinstance(d, dict) else 0),
    ("recruitee",
     "https://{slug}.recruitee.com/api/offers/",
     lambda d: len(d.get("offers", []))),
]


def _slug_guesses(name: str, extra: list[str]) -> list[str]:
    base = name.strip().lower()
    guesses = {
        base,
        re.sub(r"[^a-z0-9]", "", base),               # strip spaces/punctuation
        re.sub(r"[^a-z0-9]+", "-", base).strip("-"),  # hyphenate
        re.sub(r"\b(hq|inc|corp|ltd|canada|labs)\b", "", base).strip(),
        re.sub(r"[^a-z0-9]", "", re.sub(r"\b(hq|inc|corp|ltd|canada|labs)\b", "", base)),
    }
    guesses.update(g.lower() for g in extra)
    return [g for g in guesses if g]


def _probe(ats: str, url_tpl: str, counter, slug: str):
    url = url_tpl.format(slug=slug)
    try:
        r = httpx.get(url, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
        if r.status_code != 200:
            return (ats, slug, None, r.status_code)
        n = counter(r.json())
        return (ats, slug, n, 200)
    except Exception as e:                              # noqa: BLE001
        return (ats, slug, None, str(e)[:40])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", help="source names to find slugs for")
    ap.add_argument("--extra", nargs="*", default=[],
                    help="extra slug guesses to try for every name")
    ap.add_argument("--from-broken", action="store_true",
                    help="pull every broken source name from health automatically")
    args = ap.parse_args()

    names = list(args.names)
    if args.from_broken:
        # Pull names of sources health considers broken, so you don't type them all.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from src.store import connect
        from src import health
        broken = health.broken(connect())
        names += [b["name"] for b in broken]
        # De-dupe while preserving order.
        seen = set()
        names = [n for n in names if not (n in seen or seen.add(n))]
        print(f"Checking {len(names)} broken source(s): {', '.join(names)}")

    if not names:
        print("No names given. Pass source names or use --from-broken.")
        return 1

    for name in names:
        print(f"\n=== {name} ===")
        guesses = _slug_guesses(name, args.extra)
        hits = []
        for slug in guesses:
            for ats, url_tpl, counter in PROBES:
                ats_, slug_, n, status = _probe(ats, url_tpl, counter, slug)
                if n and n > 0:
                    hits.append((ats_, slug_, n))
                    print(f"  ✓ {ats_}:{slug_} → {n} jobs")
        if not hits:
            print(f"  no board found for any of: {', '.join(guesses)}")
            print("  → try --extra with the token from the careers URL, or it may be "
                  "Workday/SuccessFactors/custom (not one of these).")
        else:
            best = max(hits, key=lambda h: h[2])
            print(f"  BEST: ats: {best[0]}  identifier: {best[1]}  ({best[2]} jobs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
