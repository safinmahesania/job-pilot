"""Managing job sources — the company career pages and boards JobPilot fetches from.

Two lists sit behind this: the distinct sources that jobs have actually arrived from
(read from the jobs table), and the configured companies in companies.yaml that the
fetcher will try next time. Adding a source validates its ats against the adapters that
exist, so a typo is caught at the form instead of failing silently on the next fetch.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import re

from src import configio
from src.deps import _db_dep, require_admin
from src.paths import COMPANIES_FILE

router = APIRouter()


# ── Test a single source (dry run — no save, no scoring) ──

class SourceProbe(BaseModel):
    # Either point at an existing configured source by its index, or pass an inline
    # source config to try before adding it. Inline wins if both are given.
    index: int | None = None
    source: dict | None = None
    limit: int = 15          # cap the preview so a huge board doesn't flood the UI


# ── Detect the ATS + identifier from a careers URL ──

class DetectRequest(BaseModel):
    url: str


# Each rule: a regex over the URL, the ats it implies, and which capture group is the
# identifier. Ordered most-specific first. Kept here (not in the adapters) because this
# is a UI convenience — a best guess to pre-fill the form, not the source of truth.
_DETECT_RULES = [
    # (ats, compiled pattern, identifier-group)
    ("greenhouse", re.compile(r"boards\.greenhouse\.io/(?:embed/job_board\?for=)?([^/?&]+)"), 1),
    ("greenhouse", re.compile(r"job-boards\.greenhouse\.io/([^/?&]+)"), 1),
    ("lever", re.compile(r"jobs\.lever\.co/([^/?&]+)"), 1),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([^/?&]+)"), 1),
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/([^/?&]+)"), 1),
    ("smartrecruiters", re.compile(r"careers\.smartrecruiters\.com/([^/?&]+)"), 1),
    ("workable", re.compile(r"apply\.workable\.com/([^/?&]+)"), 1),
    ("workable", re.compile(r"([a-z0-9-]+)\.workable\.com"), 1),
    ("themuse", re.compile(r"themuse\.com/companies/([^/?&]+)"), 1),
    ("remotive", re.compile(r"remotive\.(?:com|io)"), None),
    ("remoteok", re.compile(r"remoteok\.(?:com|io)"), None),
    ("weworkremotely", re.compile(r"weworkremotely\.com"), None),
    ("jobspresso", re.compile(r"jobspresso\.co"), None),
]


@router.post("/api/sources/detect")
def detect_source(body: DetectRequest, _: str = Depends(require_admin)):
    """Guess the ats and identifier from a careers URL, to pre-fill the add-source form.

    A best-effort convenience: paste the link to a company's job board and the form is
    filled in for you. Workday, Oracle and Phenom need more than a slug (tenant/host/site
    or a base URL), so those are detected but flagged as needing manual completion.
    Anything unrecognised comes back as a generic HTML scrape (`custom`), which is a
    reasonable default that will at least try."""
    url = (body.url or "").strip()
    if not url:
        raise HTTPException(400, "no URL provided")

    low = url.lower()

    # Workday / Oracle / Phenom — recognisable but need structured fields, not a slug.
    if "myworkdayjobs.com" in low or ".wd" in low and "workday" in low:
        m = re.search(r"https?://([^.]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[^/]+/)?([^/?&]+)", url)
        if m:
            return {"ats": "workday", "identifier": "",
                    "tenant": m.group(1), "host": m.group(2), "site": m.group(3),
                    "needs_detail": True,
                    "note": "Workday needs tenant, host and site — pre-filled where found."}
        return {"ats": "workday", "identifier": "", "needs_detail": True,
                "note": "Looks like Workday — fill in tenant / host / site by hand."}

    if "oraclecloud.com" in low or "/hcmui/" in low:
        return {"ats": "oracle", "identifier": "", "needs_detail": True,
                "note": "Looks like Oracle Cloud — needs host and site; edit companies.yaml."}

    for ats, pattern, group in _DETECT_RULES:
        m = pattern.search(url)
        if m:
            identifier = m.group(group) if group else ""
            return {"ats": ats, "identifier": identifier, "needs_detail": False,
                    "note": None}

    # Unrecognised — fall back to the generic HTML scraper against the given URL.
    return {"ats": "custom", "identifier": "", "careers_url": url, "needs_detail": False,
            "note": "Not a known ATS — will try a generic scrape of this page."}


@router.post("/api/sources/test")
def test_source(body: SourceProbe, _: str = Depends(require_admin)):
    """Fetch one source right now and return what it found — without saving anything,
    scoring anything, or running the rest of the pipeline. This is the fast way to check
    that a source is configured correctly (right ats, right identifier/URL) before
    committing to a full run: you see the jobs it pulls, or the exact error it hits."""
    from src.adapters.base import get_adapter

    # Resolve which source to test.
    if body.source is not None:
        company = dict(body.source)
    elif body.index is not None:
        data = configio.read_yaml(COMPANIES_FILE) or {}
        items = data.get("companies", [])
        if not 0 <= body.index < len(items):
            raise HTTPException(404, "source not found")
        company = dict(items[body.index])
    else:
        raise HTTPException(400, "pass either an index or an inline source config")

    if not company.get("name"):
        company["name"] = company.get("ats", "test") + " source"

    # Build the adapter — a bad ats is a clean, expected failure, not a 500.
    try:
        adapter = get_adapter(company)
    except ValueError as e:
        return {"ok": False, "stage": "adapter", "error": str(e),
                "name": company.get("name"), "ats": company.get("ats"),
                "count": 0, "jobs": []}

    # Run the fetch. Any network / parse error is caught and reported rather than raised,
    # exactly as a real run would treat it — so testing a flaky board never 500s.
    import time
    started = time.time()
    try:
        raw = adapter.fetch()
    except Exception as e:
        return {"ok": False, "stage": "fetch", "error": f"{type(e).__name__}: {e}",
                "name": company.get("name"), "ats": company.get("ats"),
                "count": 0, "jobs": [], "elapsed_ms": round((time.time() - started) * 1000)}

    elapsed = round((time.time() - started) * 1000)
    preview = []
    for j in raw[: max(1, body.limit)]:
        preview.append({
            "title": j.get("title"),
            "location": j.get("location") or "",
            "url": j.get("source_url") or j.get("apply_url") or "",
            "has_description": bool((j.get("description") or "").strip()),
        })

    return {
        "ok": True,
        "name": company.get("name"),
        "ats": company.get("ats"),
        "count": len(raw),
        "shown": len(preview),
        "elapsed_ms": elapsed,
        "jobs": preview,
    }


@router.get("/api/sources")
def sources_list(conn=Depends(_db_dep)):
    rows = conn.execute("SELECT DISTINCT source FROM jobs ORDER BY source").fetchall()
    return [r["source"] for r in rows if r["source"]]


@router.get("/api/sources/config")
def sources_config(conn=Depends(_db_dep)):
    data = configio.read_yaml(COMPANIES_FILE) or {}

    # Pull the health verdict for every board once, keyed by name, so each configured
    # source can carry its own last-run health inline — fetched/kept counts, an ok/broken
    # status, and the detail line. This is the same data the Health view shows; surfacing
    # it next to the source config means you see, in one place, both what a source is and
    # whether it's actually working.
    from src import health
    try:
        health_by_name = {h["name"]: h for h in health.assess(conn)}
    except Exception:
        health_by_name = {}

    out = []
    for i, c in enumerate(data.get("companies", [])):
        name = c.get("name")
        h = health_by_name.get(name)
        entry = {"index": i, "name": name, "ats": c.get("ats"),
                 "active": bool(c.get("active")),
                 "identifier": c.get("identifier") or c.get("tenant") or c.get("base") or "",
                 "query": c.get("query", ""),
                 "health": None}
        if h:
            entry["health"] = {
                "verdict": h["verdict"],        # ok | wobbling | silent | never_worked | erroring
                "fetched": h["fetched"],
                "kept": h["kept"],
                "detail": h["detail"],
                "last_run": str(h["last_run"])[:19] if h["last_run"] else None,
            }
        out.append(entry)
    return out


@router.post("/api/sources/{index}/toggle")
def toggle_source(index: int, _: str = Depends(require_admin)):
    data = configio.read_yaml(COMPANIES_FILE) or {}
    items = data.get("companies", [])
    if not 0 <= index < len(items):
        raise HTTPException(404, "source not found")
    items[index]["active"] = not bool(items[index].get("active"))
    configio.write_yaml(COMPANIES_FILE, data)
    return {"index": index, "active": items[index]["active"]}


@router.post("/api/sources/prune-health")
def prune_orphaned_health(_: str = Depends(require_admin), conn=Depends(_db_dep)):
    """Delete source_health rows for boards that are no longer in the config.

    When a source is removed (or was removed before delete cleaned up health), its last
    error lingers in the Health view because health is a separate table keyed by name.
    This clears every health row whose name isn't a current board.
    """
    data = configio.read_yaml(COMPANIES_FILE) or {}
    live = {c.get("name") for c in data.get("companies", []) if c.get("name")}
    rows = conn.execute("SELECT name FROM source_health").fetchall()
    removed = [r[0] for r in rows if r[0] not in live]
    for name in removed:
        conn.execute("DELETE FROM source_health WHERE name = ?", (name,))
    conn.commit()
    return {"pruned": removed, "count": len(removed)}


class WhyEmptyProbe(BaseModel):
    index: int | None = None
    source: dict | None = None


@router.post("/api/sources/why-empty")
def why_empty(body: WhyEmptyProbe, _: str = Depends(require_admin)):
    """Fetch one source now and report which prefilter rule drops each job, so you can see
    WHY good-looking jobs never reach the feed. Nothing is saved. Mirrors the
    scripts.why_empty breakdown, but for a single source and returned as JSON."""
    from src.adapters.base import get_adapter
    from src.normalize import normalize, is_valid
    from src.scoring import prefilter

    # Resolve the source (inline config or an index into companies.yaml).
    if body.source is not None:
        company = dict(body.source)
    elif body.index is not None:
        items = (configio.read_yaml(COMPANIES_FILE) or {}).get("companies", [])
        if not 0 <= body.index < len(items):
            raise HTTPException(404, "source not found")
        company = dict(items[body.index])
    else:
        raise HTTPException(400, "pass either an index or an inline source config")

    profile = configio.read_yaml("profile.yaml") or {}
    c = profile.get("constraints", {})
    s = profile.get("search", {})

    # First failing rule wins — same order the real pipeline applies them.
    def classify(job):
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

    try:
        raw = get_adapter(company).fetch()
    except Exception as e:                             # noqa: BLE001
        return {"ok": False, "name": company.get("name"), "error": str(e)[:200],
                "total": 0, "rules": {}, "passed": 0}

    rules: dict[str, list] = {}
    total = 0
    for r in raw:
        try:
            job = normalize(r)
        except Exception:                              # noqa: BLE001
            continue
        total += 1
        verdict = classify(job)
        rules.setdefault(verdict, []).append({
            "title": (job.get("title") or "")[:80],
            "location": (job.get("location") or "")[:50],
        })

    passed = len(rules.get("passes_all", []))
    # Order rules by how many they dropped, biggest first (passes_all last).
    ordered = sorted(
        ((name, jobs) for name, jobs in rules.items() if name != "passes_all"),
        key=lambda kv: len(kv[1]), reverse=True)
    return {
        "ok": True,
        "name": company.get("name"),
        "total": total,
        "passed": passed,
        "rules": [
            {"rule": name, "count": len(jobs), "examples": jobs[:15]}
            for name, jobs in ordered
        ],
        "passed_examples": rules.get("passes_all", [])[:15],
    }


class NewSource(BaseModel):
    # A source with no name or no ats used to be accepted and written to
    # companies.yaml, where it did nothing except produce a "No adapter for ats=''"
    # error on the next fetch — with a blank name, so you could not even tell which
    # row was broken. min_length rejects the empty case at the form.
    name: str = Field(min_length=1)
    ats: str = Field(min_length=1)
    identifier: str | None = None
    tenant: str | None = None
    host: str | None = None
    site: str | None = None
    base: str | None = None
    query: str | None = None
    active: bool = True


@router.post("/api/sources")
def add_source(body: NewSource, _: str = Depends(require_admin)):
    from src.adapters.base import KNOWN_ATS

    name = body.name.strip()
    ats = body.ats.strip().lower()
    if not name:
        raise HTTPException(400, "a source needs a name")
    if ats not in KNOWN_ATS:
        raise HTTPException(
            400, f"unknown ats '{ats}' — must be one of: {', '.join(sorted(KNOWN_ATS))}")

    data = configio.read_yaml(COMPANIES_FILE) or {"companies": []}
    entry: dict = {"name": name, "ats": ats}
    for k in ("identifier", "tenant", "host", "site", "base", "query"):
        v = getattr(body, k)
        if v:
            entry[k] = v
    entry["active"] = body.active
    data.setdefault("companies", []).append(entry)
    configio.write_yaml(COMPANIES_FILE, data)
    return {"added": name, "total": len(data["companies"])}


@router.delete("/api/sources/{index}")
def delete_source(index: int, _: str = Depends(require_admin), conn=Depends(_db_dep)):
    data = configio.read_yaml(COMPANIES_FILE) or {}
    items = data.get("companies", [])
    if not 0 <= index < len(items):
        raise HTTPException(404, "source not found")
    removed = items.pop(index)
    configio.write_yaml(COMPANIES_FILE, data)
    # Health lives in its own table keyed by name; without this the deleted board keeps
    # showing its last error in the Health view forever.
    name = removed.get("name")
    if name:
        conn.execute("DELETE FROM source_health WHERE name = ?", (name,))
        conn.commit()
    return {"removed": name}
