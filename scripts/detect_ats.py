"""
ATS auto-detector for JobPilot.

Reads a seed list (Company Name | careers URL), visits each careers page,
detects which ATS the company uses, extracts the board token/identifier,
verifies it against the ATS public API where possible, and writes
config-ready companies.yaml entries.

Usage:
    python scripts/detect_ats.py                 # uses canadian_companies.txt
    python scripts/detect_ats.py mylist.txt      # custom seed file

Output:
    discovered_companies.yaml   -> paste the entries into your companies.yaml
    (unresolved companies are printed at the end for manual checking)

Adapters you already have: greenhouse, lever, workday, oracle, phenom.
Detected-but-no-adapter types (ashby, workable, smartrecruiters, recruitee,
bamboohr, icims, successfactors) are still written, marked so you know they
need an adapter before enabling.
"""
import re
import sys
import pathlib
import time
import httpx

# Seed and output live in scripts/seeds/ next to this file.
SEEDS_DIR = pathlib.Path(__file__).resolve().parent / "seeds"
SEED = sys.argv[1] if len(sys.argv) > 1 else str(SEEDS_DIR / "canadian_companies.txt")
HEADERS = {"User-Agent": "Mozilla/5.0 (JobPilot ATS detector)"}
HAVE_ADAPTER = {"greenhouse", "lever", "workday", "oracle", "phenom"}


def fetch(url):
    try:
        r = httpx.get(url, headers=HEADERS, timeout=25, follow_redirects=True)
        return str(r.url), r.text
    except Exception as e:
        return None, f"ERROR: {e}"


def detect(final_url, html):
    """Return (ats, fields_dict) or (None, {})."""
    blob = f"{final_url}\n{html}"

    m = re.search(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-zA-Z0-9_]+)", blob)
    if m:
        return "greenhouse", {"identifier": m.group(1)}

    m = re.search(r"jobs\.lever\.co/([a-zA-Z0-9\-]+)", blob)
    if m:
        return "lever", {"identifier": m.group(1)}

    m = re.search(r"jobs\.ashbyhq\.com/([a-zA-Z0-9\-]+)", blob)
    if m:
        return "ashby", {"identifier": m.group(1)}

    m = re.search(r"([a-z0-9\-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-zA-Z\-]+/)?([A-Za-z0-9_]+)", blob)
    if m:
        return "workday", {"tenant": m.group(1), "host": m.group(2), "site": m.group(3)}

    m = re.search(r"([a-z0-9\-]+\.fa\.oraclecloud\.com)/hcmUI/CandidateExperience/[a-zA-Z\-]+/sites/([A-Za-z0-9_]+)", blob)
    if m:
        return "oracle", {"host": m.group(1), "site": m.group(2)}

    if "/search-jobs/" in blob:
        base = re.match(r"(https?://[^/]+)", final_url)
        return "phenom", {"base": base.group(1) if base else final_url}

    m = re.search(r"([a-zA-Z0-9\-]+)\.workable\.com", blob) or re.search(r"apply\.workable\.com/([a-zA-Z0-9\-]+)", blob)
    if m:
        return "workable", {"identifier": m.group(1)}

    m = re.search(r"careers\.smartrecruiters\.com/([A-Za-z0-9]+)", blob) or re.search(r"jobs\.smartrecruiters\.com/([A-Za-z0-9]+)", blob)
    if m:
        return "smartrecruiters", {"identifier": m.group(1)}

    m = re.search(r"([a-z0-9\-]+)\.recruitee\.com", blob)
    if m:
        return "recruitee", {"identifier": m.group(1)}

    m = re.search(r"([a-z0-9\-]+)\.bamboohr\.com", blob)
    if m:
        return "bamboohr", {"identifier": m.group(1)}

    if "icims.com" in blob:
        return "icims", {}
    if "careersection" in blob.lower() or "successfactors" in blob.lower():
        return "successfactors", {}

    return None, {}


def verify(ats, f):
    """Return (ok: bool, note: str). Only greenhouse/lever/ashby are cheap to verify."""
    try:
        if ats == "greenhouse":
            r = httpx.get(f"https://boards-api.greenhouse.io/v1/boards/{f['identifier']}/jobs",
                          headers=HEADERS, timeout=20)
            if r.status_code == 200:
                return True, f"{len(r.json().get('jobs', []))} jobs"
        elif ats == "lever":
            r = httpx.get(f"https://api.lever.co/v0/postings/{f['identifier']}?mode=json",
                          headers=HEADERS, timeout=20)
            if r.status_code == 200:
                return True, f"{len(r.json())} jobs"
        elif ats == "ashby":
            r = httpx.get(f"https://api.ashbyhq.com/posting-api/job-board/{f['identifier']}",
                          headers=HEADERS, timeout=20)
            if r.status_code == 200:
                return True, f"{len(r.json().get('jobs', []))} jobs"
        else:
            return True, "not auto-verified"
    except Exception as e:
        return False, f"verify failed: {e}"
    return False, "verify returned non-200"


def yaml_entry(name, ats, f, note):
    lines = [f"  - name: {name}", f"    ats: {ats}"]
    for k in ("identifier", "tenant", "host", "site", "base"):
        if k in f:
            lines.append(f"    {k}: {f[k]}")
    if ats in ("greenhouse", "lever", "workday", "oracle", "phenom"):
        lines.append("    query: developer")
    lines.append("    active: false")
    tag = "" if ats in HAVE_ADAPTER else "  # NO ADAPTER YET"
    lines.append(f"    # {ats} | {note}{tag}")
    return "\n".join(lines)


def main():
    companies = []
    with open(SEED, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "|" not in line:
                continue
            name, url = [x.strip() for x in line.split("|", 1)]
            companies.append((name, url))

    resolved, unresolved = [], []
    for name, url in companies:
        print(f"→ {name:22} ", end="", flush=True)
        final_url, html = fetch(url)
        if final_url is None:
            print("fetch failed")
            unresolved.append((name, url, "fetch failed"))
            continue
        ats, f = detect(final_url, html)
        if not ats:
            print("no ATS detected")
            unresolved.append((name, url, "no ATS detected"))
            continue
        ok, note = verify(ats, f)
        status = "OK" if ok else "unverified"
        print(f"{ats:15} {status}  ({note})")
        resolved.append((name, ats, f, note, ok))
        time.sleep(0.3)

    # write yaml (verified first)
    with open(SEEDS_DIR / "discovered_companies.yaml", "w", encoding="utf-8") as out:
        out.write("# Auto-detected companies. Review, then set active: true on the ones you want.\n")
        out.write("# Entries marked 'NO ADAPTER YET' need an adapter before they'll fetch.\n\n")
        out.write("companies:\n")
        for name, ats, f, note, ok in sorted(resolved, key=lambda x: (not x[4], x[1])):
            out.write(yaml_entry(name, ats, f, note) + "\n\n")

    print("\n" + "=" * 50)
    print(f"Resolved: {len(resolved)}  |  Unresolved: {len(unresolved)}")
    print("Written -> scripts/seeds/discovered_companies.yaml")
    if unresolved:
        print("\nUnresolved (check careers page manually):")
        for name, url, why in unresolved:
            print(f"  - {name}: {why}  ({url})")


if __name__ == "__main__":
    main()
