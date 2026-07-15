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


@router.get("/api/sources")
def sources_list(conn=Depends(_db_dep)):
    rows = conn.execute("SELECT DISTINCT source FROM jobs ORDER BY source").fetchall()
    return [r["source"] for r in rows if r["source"]]


@router.get("/api/sources/config")
def sources_config():
    data = configio.read_yaml("companies.yaml") or {}
    out = []
    for i, c in enumerate(data.get("companies", [])):
        out.append({"index": i, "name": c.get("name"), "ats": c.get("ats"),
                    "active": bool(c.get("active")),
                    "identifier": c.get("identifier") or c.get("tenant") or c.get("base") or "",
                    "query": c.get("query", "")})
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
