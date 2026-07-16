"""Managing job sources — the company career pages and boards JobPilot fetches from.

Two lists sit behind this: the distinct sources that jobs have actually arrived from
(read from the jobs table), and the configured companies in companies.yaml that the
fetcher will try next time. Adding a source validates its ats against the adapters that
exist, so a typo is caught at the form instead of failing silently on the next fetch.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src import configio
from src.deps import _db_dep

router = APIRouter()


# ── Test a single source (dry run — no save, no scoring) ──

class SourceProbe(BaseModel):
    # Either point at an existing configured source by its index, or pass an inline
    # source config to try before adding it. Inline wins if both are given.
    index: int | None = None
    source: dict | None = None
    limit: int = 15          # cap the preview so a huge board doesn't flood the UI


@router.post("/api/sources/test")
def test_source(body: SourceProbe):
    """Fetch one source right now and return what it found — without saving anything,
    scoring anything, or running the rest of the pipeline. This is the fast way to check
    that a source is configured correctly (right ats, right identifier/URL) before
    committing to a full run: you see the jobs it pulls, or the exact error it hits."""
    from src.adapters.base import get_adapter

    # Resolve which source to test.
    if body.source is not None:
        company = dict(body.source)
    elif body.index is not None:
        data = configio.read_yaml("companies.yaml") or {}
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
    data = configio.read_yaml("companies.yaml") or {}

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
def toggle_source(index: int):
    data = configio.read_yaml("companies.yaml") or {}
    items = data.get("companies", [])
    if not 0 <= index < len(items):
        raise HTTPException(404, "source not found")
    items[index]["active"] = not bool(items[index].get("active"))
    configio.write_yaml("companies.yaml", data)
    return {"index": index, "active": items[index]["active"]}


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
def add_source(body: NewSource):
    from src.adapters.base import KNOWN_ATS

    name = body.name.strip()
    ats = body.ats.strip().lower()
    if not name:
        raise HTTPException(400, "a source needs a name")
    if ats not in KNOWN_ATS:
        raise HTTPException(
            400, f"unknown ats '{ats}' — must be one of: {', '.join(sorted(KNOWN_ATS))}")

    data = configio.read_yaml("companies.yaml") or {"companies": []}
    entry: dict = {"name": name, "ats": ats}
    for k in ("identifier", "tenant", "host", "site", "base", "query"):
        v = getattr(body, k)
        if v:
            entry[k] = v
    entry["active"] = body.active
    data.setdefault("companies", []).append(entry)
    configio.write_yaml("companies.yaml", data)
    return {"added": name, "total": len(data["companies"])}


@router.delete("/api/sources/{index}")
def delete_source(index: int):
    data = configio.read_yaml("companies.yaml") or {}
    items = data.get("companies", [])
    if not 0 <= index < len(items):
        raise HTTPException(404, "source not found")
    removed = items.pop(index)
    configio.write_yaml("companies.yaml", data)
    return {"removed": removed.get("name")}
