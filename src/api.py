"""FastAPI backend for JobPilot — serves jobs, status updates, and the frontend."""
import sqlite3
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import threading
from datetime import datetime
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, PlainTextResponse
from src import maintenance, scheduler, configio
from src.paths import DB_PATH as DB
app = FastAPI(title="JobPilot")

COLS = ("id, title, company, location, remote, job_type, source, source_url, "
        "apply_url, description, posted_date, deadline, score, skills_score, "
        "seniority_score, domain_score, rationale, status, applied_on, notes")

# feed = naye/undecided | saved | applied ; dismissed kahin nahi dikhta
TAB_WHERE = {
    "feed": "status = 'surfaced'",
    "saved": "status = 'saved'",
    "applied": "status = 'applied'",
    "dismissed": "status = 'dismissed'",
}

ALLOWED_STATUS = {"surfaced", "saved", "applied", "dismissed",
                  "interview", "offer", "rejected"}


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


# ---- pipeline run state (in-memory) ----

@app.get("/api/health")
def source_health():
    conn = _conn()
    rows = conn.execute(
        "SELECT name, ats, fetched, kept, status, error, last_run "
        "FROM source_health ORDER BY status DESC, fetched DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


@app.get("/api/counts")
def counts():
    conn = _conn()
    threshold = int(_get_setting(conn, "score_threshold", 70))
    out = {
        "feed": conn.execute(f"SELECT COUNT(*) FROM jobs WHERE status='surfaced' AND score >= {threshold}").fetchone()[
            0],
        "saved": conn.execute("SELECT COUNT(*) FROM jobs WHERE status='saved'").fetchone()[0],
        "applied": conn.execute("SELECT COUNT(*) FROM jobs WHERE status='applied'").fetchone()[0],
        "dismissed": conn.execute("SELECT COUNT(*) FROM jobs WHERE status='dismissed'").fetchone()[0],
    }
    conn.close()
    return out


@app.get("/api/settings")
def get_settings():
    conn = _conn()
    t = int(_get_setting(conn, "score_threshold", 70))
    conn.close()
    return {"score_threshold": t}


class ThresholdUpdate(BaseModel):
    value: int


@app.post("/api/settings/threshold")
def set_threshold(body: ThresholdUpdate):
    v = max(0, min(100, body.value))
    conn = _conn()
    conn.execute("INSERT INTO settings (key,value) VALUES ('score_threshold',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(v),))
    conn.commit()
    conn.close()
    return {"score_threshold": v}


@app.get("/api/stats")
def stats():
    conn = _conn()
    threshold = int(_get_setting(conn, "score_threshold", 70))
    q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]

    # funnel (status counts)
    statuses = ["surfaced", "saved", "applied", "interview", "offer", "rejected", "dismissed"]
    funnel = {s: q("SELECT COUNT(*) FROM jobs WHERE status=?", s) for s in statuses}

    # volume
    total = q("SELECT COUNT(*) FROM jobs")
    avg_score = q("SELECT ROUND(AVG(score),1) FROM jobs") or 0
    feed_size = q("SELECT COUNT(*) FROM jobs WHERE status='surfaced' AND score>=?", threshold)

    # score distribution
    dist = {
        "80+": q("SELECT COUNT(*) FROM jobs WHERE score>=80"),
        "70-79": q("SELECT COUNT(*) FROM jobs WHERE score>=70 AND score<80"),
        "60-69": q("SELECT COUNT(*) FROM jobs WHERE score>=60 AND score<70"),
        "<60": q("SELECT COUNT(*) FROM jobs WHERE score<60"),
    }

    # source breakdown (jobs table se, avg score + count per source)
    src_rows = conn.execute(
        "SELECT source, COUNT(*) c, ROUND(AVG(score),1) avg FROM jobs "
        "GROUP BY source ORDER BY c DESC"
    ).fetchall()
    sources = [dict(r) for r in src_rows]

    # deadlines — agle 14 din / expired (deadline text hai, isliye date-compare best-effort)
    deadline_rows = conn.execute(
        "SELECT title, company, deadline FROM jobs "
        "WHERE deadline IS NOT NULL AND deadline != '' ORDER BY deadline ASC LIMIT 10"
    ).fetchall()
    deadlines = [dict(r) for r in deadline_rows]

    # conversion rates
    def pct(a, b): return round(100 * a / b, 1) if b else 0

    applied = funnel["applied"] + funnel["interview"] + funnel["offer"]  # applied+ aage bhi
    rates = {
        "applied_of_total": pct(applied, total),
        "interview_of_applied": pct(funnel["interview"] + funnel["offer"], applied),
        "offer_of_interview": pct(funnel["offer"], funnel["interview"] + funnel["offer"]),
    }

    conn.close()
    return {
        "funnel": funnel, "total": total, "avg_score": avg_score,
        "feed_size": feed_size, "distribution": dist, "sources": sources,
        "deadlines": deadlines, "rates": rates,
    }


@app.post("/api/maint/rescore")
def maint_rescore():
    return maintenance.rescore_all()


@app.post("/api/maint/cleanup")
def maint_cleanup():
    return maintenance.cleanup_below_threshold()


class DaysBody(BaseModel):
    days: int = 30


@app.post("/api/maint/clear-old")
def maint_clear_old(body: DaysBody):
    return maintenance.clear_old_jobs(body.days)


@app.get("/api/maint/export")
def maint_export():
    csv_data = maintenance.export_csv()
    return Response(content=csv_data, media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=jobpilot_jobs.csv"})


@app.post("/api/maint/reload")
def maint_reload():
    return maintenance.reload_config()


@app.post("/api/maint/clean-cache")
def maint_clean_cache():
    return maintenance.clean_cache()


@app.post("/api/maint/reset")
def maint_reset():
    return maintenance.reset_all_jobs()

@app.get("/api/jobs")
def list_jobs(tab: str = "feed", sort: str = "score", source: str = "all"):
    conn = _conn()
    threshold = int(_get_setting(conn, "score_threshold", 70))

    where = (f"status='surfaced' AND score >= {threshold}"
             if tab == "feed" else TAB_WHERE.get(tab, TAB_WHERE["feed"]))

    params = []
    if source and source != "all":
        where += " AND source = ?"
        params.append(source)

    order = {"score": "score DESC", "newest": "posted_date DESC",
             "company": "company ASC"}.get(sort, "score DESC")   # whitelist, safe

    rows = conn.execute(f"SELECT {COLS} FROM jobs WHERE {where} ORDER BY {order}",
                        params).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/sources")
def sources_list():
    conn = _conn()
    rows = conn.execute("SELECT DISTINCT source FROM jobs ORDER BY source").fetchall()
    conn.close()
    return [r["source"] for r in rows if r["source"]]


@app.on_event("startup")
def _start_scheduler():
    scheduler.start()


# ───────────────────────── pipeline runs ─────────────────────────

@app.post("/api/run")
def trigger_run():
    if not scheduler.trigger_async():
        raise HTTPException(409, "pipeline already running")
    return {"started": True}


@app.get("/api/run/status")
def run_status():
    return scheduler.get_state()


# ───────────────────────── schedule config ─────────────────────────

@app.get("/api/schedule")
def get_schedule():
    conn = _conn()
    enabled = _get_setting(conn, "scheduler_enabled", "1") == "1"
    hours = float(_get_setting(conn, "run_interval_hours", "8") or 8)
    conn.close()
    s = scheduler.get_state()
    return {"enabled": enabled, "interval_hours": hours,
            "last_run": s["last_run"], "next_run": s["next_run"], "running": s["running"]}


class ScheduleUpdate(BaseModel):
    enabled: bool
    interval_hours: float


@app.post("/api/schedule")
def set_schedule(body: ScheduleUpdate):
    hours = max(0.5, min(168.0, body.interval_hours))
    conn = _conn()
    for k, v in (("scheduler_enabled", "1" if body.enabled else "0"),
                 ("run_interval_hours", str(hours))):
        conn.execute("INSERT INTO settings (key,value) VALUES (?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    conn.commit()
    conn.close()
    return {"enabled": body.enabled, "interval_hours": hours}


# ───────────────────────── sources (companies.yaml) ─────────────────────────

@app.get("/api/sources/config")
def sources_config():
    data = configio.read_yaml("companies.yaml") or {}
    out = []
    for i, c in enumerate(data.get("companies", [])):
        out.append({"index": i, "name": c.get("name"), "ats": c.get("ats"),
                    "active": bool(c.get("active")),
                    "identifier": c.get("identifier") or c.get("tenant") or c.get("base") or "",
                    "query": c.get("query", "")})
    return out


@app.post("/api/sources/{index}/toggle")
def toggle_source(index: int):
    data = configio.read_yaml("companies.yaml") or {}
    items = data.get("companies", [])
    if not 0 <= index < len(items):
        raise HTTPException(404, "source not found")
    items[index]["active"] = not bool(items[index].get("active"))
    configio.write_yaml("companies.yaml", data)
    return {"index": index, "active": items[index]["active"]}


class NewSource(BaseModel):
    name: str
    ats: str
    identifier: str | None = None
    tenant: str | None = None
    host: str | None = None
    site: str | None = None
    base: str | None = None
    query: str | None = None
    active: bool = True


@app.post("/api/sources")
def add_source(body: NewSource):
    data = configio.read_yaml("companies.yaml") or {"companies": []}
    entry = {"name": body.name, "ats": body.ats}
    for k in ("identifier", "tenant", "host", "site", "base", "query"):
        v = getattr(body, k)
        if v:
            entry[k] = v
    entry["active"] = body.active
    data.setdefault("companies", []).append(entry)
    configio.write_yaml("companies.yaml", data)
    return {"added": body.name, "total": len(data["companies"])}


@app.delete("/api/sources/{index}")
def delete_source(index: int):
    data = configio.read_yaml("companies.yaml") or {}
    items = data.get("companies", [])
    if not 0 <= index < len(items):
        raise HTTPException(404, "source not found")
    removed = items.pop(index)
    configio.write_yaml("companies.yaml", data)
    return {"removed": removed.get("name")}


# ───────────────────────── profile.yaml ─────────────────────────

@app.get("/api/profile")
def get_profile():
    return {"data": configio.read_yaml("profile.yaml") or {}}


class ProfileData(BaseModel):
    data: dict


@app.post("/api/profile")
def save_profile(body: ProfileData):
    current = configio.read_yaml("profile.yaml") or {}
    current.update(body.data)          # only the keys the form manages
    configio.write_yaml("profile.yaml", current)
    return {"saved": True}


# Raw YAML escape hatch — for the fields the form doesn't cover.

@app.get("/api/profile/raw")
def get_profile_raw():
    return {"text": configio.read_text("profile.yaml")}


class ProfileText(BaseModel):
    text: str


@app.post("/api/profile/raw")
def save_profile_raw(body: ProfileText):
    try:
        configio.write_text("profile.yaml", body.text)
    except Exception as e:
        raise HTTPException(400, f"invalid YAML: {e}")
    return {"saved": True}

class StatusUpdate(BaseModel):
    status: str

@app.post("/api/jobs/{job_id}/status")
def set_status(job_id: int, body: StatusUpdate):
    if body.status not in ALLOWED_STATUS:
        raise HTTPException(400, f"invalid status: {body.status}")
    conn = _conn()
    if body.status == "applied":
        cur = conn.execute(
            "UPDATE jobs SET status=?, applied_on=COALESCE(applied_on, date('now')) WHERE id=?",
            (body.status, job_id),
        )
    else:
        cur = conn.execute("UPDATE jobs SET status=? WHERE id=?", (body.status, job_id))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    if not changed:
        raise HTTPException(404, "job not found")
    return {"id": job_id, "status": body.status}


class NotesUpdate(BaseModel):
    notes: str

@app.post("/api/jobs/{job_id}/notes")
def set_notes(job_id: int, body: NotesUpdate):
    conn = _conn()
    conn.execute("UPDATE jobs SET notes=? WHERE id=?", (body.notes, job_id))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/api/runs")
def list_runs(limit: int = 20):
    conn = _conn()
    rows = conn.execute(
        "SELECT id, started_at, kind, fetched, seen, dropped, trashed, kept, errors "
        "FROM runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/model")
def model_state():
    from src.scoring.rerank import get_model_state
    return get_model_state()

class ModelUpdate(BaseModel):
    model: str


@app.post("/api/model")
def set_model(body: ModelUpdate):
    from src.scoring.rerank import set_preferred, get_model_state
    set_preferred(body.model)
    conn = _conn()
    conn.execute("INSERT INTO settings (key,value) VALUES ('scoring_model',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (body.model,))
    conn.commit()
    conn.close()
    return get_model_state()

@app.get("/api/notify")
def get_notify():
    from src import notify
    conn = _conn()
    enabled = _get_setting(conn, "notify_enabled", "1") == "1"
    conn.close()
    return {"enabled": enabled, "configured": bool(notify.TOKEN and notify.CHAT_ID)}


class NotifyUpdate(BaseModel):
    enabled: bool


@app.post("/api/notify")
def set_notify(body: NotifyUpdate):
    conn = _conn()
    conn.execute("INSERT INTO settings (key,value) VALUES ('notify_enabled',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 ("1" if body.enabled else "0",))
    conn.commit()
    conn.close()
    return {"enabled": body.enabled}


@app.post("/api/notify/test")
def test_notify():
    from src import notify
    ok = notify.send("JobPilot test — notifications working ✅")
    return {"sent": ok}

# ── AI providers (status, enable/disable, reorder) ──────────────────────────

@app.get("/api/llm/providers")
def llm_providers():
    """Status of every generation provider: config, quota usage, enabled."""
    from src import llm
    providers = llm.provider_status()
    tracked = [p for p in providers if p["daily_tokens"]]
    return {
        "providers": providers,
        "available": sum(1 for p in providers if p["configured"] and p["enabled"]),
        "total": len(providers),
        "combined_tokens": sum(p["tokens_used"] for p in tracked),
        "combined_limit": sum(p["daily_tokens"] for p in tracked),
    }


class ProviderToggle(BaseModel):
    enabled: bool


@app.post("/api/llm/providers/{name}/toggle")
def llm_provider_toggle(name: str, body: ProviderToggle):
    from src import llm
    from src.paths import LLM_PROVIDERS
    if name not in LLM_PROVIDERS:
        raise HTTPException(404, "unknown provider")
    llm.set_enabled(name, body.enabled)
    return {"name": name, "enabled": body.enabled}


class ProviderOrder(BaseModel):
    order: list[str]


@app.post("/api/llm/providers/order")
def llm_provider_order(body: ProviderOrder):
    """Reorder the fallback chain (first = tried first)."""
    from src import llm
    llm.set_order(body.order)
    return {"order": llm.get_order()}


# ── Storage & cleanup ───────────────────────────────────────────────────────

@app.post("/api/maint/clear-runs")
def maint_clear_runs():
    return maintenance.clear_run_history()


@app.post("/api/maint/nuclear")
def maint_nuclear():
    return maintenance.nuclear_reset()


@app.post("/api/jobs/{job_id}/cover-letter")
def cover_letter(job_id: int):
    """Generate a grounded cover letter for one job."""
    conn = _conn()
    row = conn.execute(
        "SELECT title, company, description FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "job not found")
    from src import apply
    try:
        result = apply.generate_cover_letter(dict(row))
    except Exception as e:
        raise HTTPException(502, f"generation failed: {e}")
    return result


app.mount("/", StaticFiles(
    directory=str(Path(__file__).parent.parent / "frontend"),
    html=True,
), name="frontend")
