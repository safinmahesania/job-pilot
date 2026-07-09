"""
Board-token harvester for JobPilot.

Instead of detecting a company's ATS from its (often JS-rendered) careers page,
this guesses likely board tokens from the company name and *verifies* each one
against the public Greenhouse / Lever / Ashby APIs. A 200 + job list means the
token is real. No Playwright, no scraping.

    python scripts/harvest_board_tokens.py                  # uses company_seed.txt
    python scripts/harvest_board_tokens.py myseed.txt

Writes harvested_companies.yaml, sorted so boards with the most CANADIAN jobs
come first. Boards with zero Canadian jobs today are written active: false
(their postings may still change — flip them on if you want).

Limitation: tokens unrelated to the company name (e.g. Properly -> "pine")
cannot be guessed. Use ats_detect.py for those.
"""
import re
import sys
import pathlib
import time
import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed

# Seed and output live in scripts/seeds/ next to this file.
SEEDS_DIR = pathlib.Path(__file__).resolve().parent / "seeds"
SEED = sys.argv[1] if len(sys.argv) > 1 else str(SEEDS_DIR / "company_seed.txt")
HEADERS = {"User-Agent": "Mozilla/5.0 (JobPilot harvester)", "Accept": "application/json"}
WORKERS = 8
TIMEOUT = 12

CA = ("canada", "canadian", "toronto", "montreal", "montréal", "vancouver",
      "ottawa", "calgary", "waterloo", "kitchener", "edmonton", "quebec",
      "québec", "ontario", "british columbia", "alberta", "halifax",
      "winnipeg", "victoria", "mississauga", "burnaby", "remote - canada")

DROP = {"inc", "incorporated", "corp", "corporation", "ltd", "limited", "co",
        "technologies", "technology", "software", "labs", "systems",
        "solutions", "group", "the", "app", "media"}


def variants(name: str) -> list[str]:
    clean = re.sub(r"[^A-Za-z0-9 ]", "", name).strip()
    words = clean.lower().split()
    core = [w for w in words if w not in DROP] or words
    joined, hyph = "".join(core), "-".join(core)
    allw = "".join(words)
    out = [joined, hyph, allw, joined + "inc", joined + "hq"]
    # Ashby tokens are case-sensitive; try the original casing too
    out.append(re.sub(r"\s+", "", clean))
    seen, uniq = set(), []
    for v in out:
        if len(v) >= 2 and v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq[:6]


def _jobs_greenhouse(t):
    r = httpx.get(f"https://boards-api.greenhouse.io/v1/boards/{t}/jobs",
                  headers=HEADERS, timeout=TIMEOUT)
    return [j.get("location", {}).get("name", "") for j in r.json().get("jobs", [])] if r.status_code == 200 else None


def _jobs_lever(t):
    r = httpx.get(f"https://api.lever.co/v0/postings/{t}?mode=json",
                  headers=HEADERS, timeout=TIMEOUT)
    if r.status_code != 200:
        return None
    d = r.json()
    return [j.get("categories", {}).get("location", "") for j in d] if isinstance(d, list) else None


def _jobs_ashby(t):
    r = httpx.get(f"https://api.ashbyhq.com/posting-api/job-board/{t}",
                  headers=HEADERS, timeout=TIMEOUT)
    return [j.get("location", "") for j in r.json().get("jobs", [])] if r.status_code == 200 else None


CHECKS = {"greenhouse": _jobs_greenhouse, "lever": _jobs_lever, "ashby": _jobs_ashby}


def probe(name):
    """Return first (ats, token, total, canadian) hit, or None."""
    for token in variants(name):
        for ats, fn in CHECKS.items():
            try:
                locs = fn(token)
            except Exception:
                continue
            if locs is None:
                continue
            total = len(locs)
            if total == 0:
                continue           # empty board: token may be wrong, keep trying
            ca = sum(1 for l in locs if any(k in (l or "").lower() for k in CA))
            return ats, token, total, ca
            time.sleep(0.05)
    return None


def main():
    names = []
    with open(SEED, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                names.append(line)

    print(f"Probing {len(names)} companies across greenhouse/lever/ashby…\n")
    hits, misses = [], []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(probe, n): n for n in names}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                res = fut.result()
            except Exception:
                res = None
            if res:
                ats, token, total, ca = res
                hits.append((name, ats, token, total, ca))
                print(f"  HIT  {name:22} {ats:11} {token:20} {total:4} jobs  ({ca} in Canada)")
            else:
                misses.append(name)

    hits.sort(key=lambda h: (-h[4], -h[3]))

    with open(SEEDS_DIR / "harvested_companies.yaml", "w", encoding="utf-8") as out:
        out.write("# Auto-harvested + API-verified board tokens.\n")
        out.write("# Sorted by number of Canadian postings at harvest time.\n")
        out.write("# active: false = zero Canadian jobs right now (may change).\n\n")
        out.write("companies:\n")
        for name, ats, token, total, ca in hits:
            out.write(f"  - name: {name}\n")
            out.write(f"    ats: {ats}\n")
            out.write(f"    identifier: {token}\n")
            out.write(f"    active: {'true' if ca else 'false'}\n")
            out.write(f"    # {total} jobs, {ca} in Canada\n\n")

    print("\n" + "=" * 55)
    print(f"Verified boards: {len(hits)}   |   No token found: {len(misses)}")
    print(f"With Canadian jobs today: {sum(1 for h in hits if h[4])}")
    print("Written -> scripts/seeds/harvested_companies.yaml")
    if misses:
        print("\nNo token guessable (run ats_detect.py on these, or check manually):")
        print("  " + ", ".join(misses))


if __name__ == "__main__":
    main()
