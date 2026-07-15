"""FastAPI backend for JobPilot — serves jobs, status updates, and the frontend."""
import re
from pathlib import Path
import os
import secrets
from fastapi import FastAPI, HTTPException, UploadFile, File, Request, Depends
from pydantic import BaseModel, Field
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from src import maintenance, scheduler, configio, store
from src import resume_guard
from src import resume_fit
from src.paths import MAX_UPLOAD_BYTES
from src.deps import (_db_dep, _get_setting,
                      COLS, TAB_WHERE, ALLOWED_STATUS)
from src.logs import log
from src import __version__
from src.env import load_env
load_env()
from src.paths import (RATE_LIMIT_GENERATION, RATE_LIMIT_IMPORT, RATE_LIMIT_DEFAULT)
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
app = FastAPI(title="JobPilot", version=__version__)

# Rate limiting. Keyed by client address, with a generous global default; the
# expensive endpoints (generation, import) get tighter per-route limits via the
# @limiter.limit decorator. A single real user never approaches these — they exist so
# a public tunnel URL cannot be turned into free LLM compute or a parse-DoS by a bot.
limiter = Limiter(key_func=get_remote_address, default_limits=[RATE_LIMIT_DEFAULT])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# The browser extension runs on ATS pages and calls this API from a
# chrome-extension:// origin, so those requests must be allowed through.
# CORS for the browser extension only.
#
# The extension runs on ATS pages and calls this API from a chrome-extension:// (or
# moz-extension://) origin, so those origins are allowed. Two deliberate narrowings
# from the audit:
#
#   allow_credentials stays False, so this policy never lets another origin send the
#   jp_auth cookie — cross-origin requests can only authenticate with the explicit
#   x-jobpilot-key header, which the extension sets and a random page does not.
#
#   the methods and headers are named, not "*". A wildcard invites any extension to
#   send anything; the app only needs these. The real defence is still the auth gate
#   on the server — CORS is a browser-side courtesy, not a lock — but there is no
#   reason to hold the door wider than the extension uses.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://[a-p]{32}|moz-extension://[0-9a-f-]{36}",
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "x-jobpilot-key"],
)


# ── A lock on the door ─────────────────────────────────────────────────────────
#
# This app has a person's phone number and home address in it, and a reset endpoint
# that empties the database with no confirmation. On a public URL, the URL is not a
# secret — bots sweep the whole internet — so "nobody knows the address" is not a
# defence.
#
# Cloudflare Access sits in front of this and asks for a Google login before a
# request ever reaches here. This is the second lock, in case Access is ever
# misconfigured or the tunnel is pointed straight at the app: set JOBPILOT_PASSWORD
# and every request must carry it.
#
# Unset, the gate is open — so local development, where the app is only ever on
# localhost, is unchanged. It is the deploy step that sets the password.
def _password() -> str:
    """Read the password at request time, not import time.

    Bound at import, the gate would freeze whatever the environment held when
    src.api was first imported — making it depend on import order and forcing a test
    that sets the password to reload the whole module. Reading it per request costs
    nothing and always reflects the environment as it is."""
    return os.environ.get("JOBPILOT_PASSWORD", "").strip()

# The extension calls the API from a chrome-extension:// origin and cannot carry a
# cookie, so it authenticates with the same password as a header instead.
_OPEN_PATHS = ("/api/health", "/healthz", "/api/version")


@app.middleware("http")
async def _gate(request: Request, call_next):
    # The audit found the previous version of this backwards, and it is worth writing
    # down so it does not come back.
    #
    # It trusted "the request arrived on localhost" as proof the request was local.
    # But cloudflared connects to the app ON localhost: every tunnel request arrives
    # from 127.0.0.1. So the rule was not "trust the extension", it was "trust
    # anything coming through the tunnel" — the exact opposite of the point. Stripping
    # one forwarded header opened the gate onto /api/maint/reset.
    #
    # There is no network fact that distinguishes the owner from an attacker here;
    # both are on the far side of the same tunnel. Only the password does. So no IP is
    # trusted. Proof is the cookie (a browser that logged in) or the header (the
    # extension, carrying the key on every call). Nothing else gets in.
    password = _password()
    if not password:
        return await call_next(request)

    path = request.url.path

    # A tiny, fixed set of endpoints that must answer before login: the health check
    # a monitor hits, and the login form and its POST.
    if path in _OPEN_PATHS or path in ("/login", "/api/login"):
        return await call_next(request)

    cookie = request.cookies.get("jp_auth", "")
    header = request.headers.get("x-jobpilot-key", "")
    if secrets.compare_digest(cookie, password) or \
       secrets.compare_digest(header, password):
        return await call_next(request)

    # A browser asking for a page gets the login screen; anything else gets a 401.
    if path.startswith("/api/"):
        return Response('{"detail":"unauthorized"}', status_code=401,
                        media_type="application/json")
    return Response(_LOGIN_HTML, status_code=401, media_type="text/html")


_LOGIN_HTML = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>JobPilot</title>
<style>body{font-family:system-ui;background:#0f1115;color:#e7e7e7;display:grid;
place-items:center;height:100vh;margin:0}form{background:#1a1d24;padding:2rem;
border-radius:12px;width:min(90vw,320px)}h1{font-size:1rem;margin:0 0 1rem}
input{width:100%;box-sizing:border-box;padding:.7rem;border-radius:8px;border:1px
solid #333;background:#0f1115;color:#e7e7e7;margin-bottom:.8rem}button{width:100%;
padding:.7rem;border:0;border-radius:8px;background:#c9a227;color:#0f1115;
font-weight:600;cursor:pointer}</style></head><body>
<form method=post action=/api/login>
<h1>JobPilot</h1>
<input type=password name=password placeholder=Password autofocus>
<button>Enter</button></form></body></html>"""


@app.post("/api/login")
async def _login(request: Request):
    form = await request.form()
    password = _password()
    if password and secrets.compare_digest(str(form.get("password", "")), password):
        r = Response(status_code=303)
        r.headers["Location"] = "/"
        # Session cookie, http-only, and marked secure so it only ever travels over
        # HTTPS — which the tunnel always is.
        r.set_cookie("jp_auth", password, httponly=True, secure=True,
                     samesite="lax", max_age=60 * 60 * 24 * 30)
        return r
    r = Response(_LOGIN_HTML, status_code=401, media_type="text/html")
    return r




# ---- pipeline run state (in-memory) ----

@app.get("/api/version")
def version():
    """What's running. Handy when a deployment might be behind the repo."""
    return {"version": __version__}


@app.get("/api/health")
def source_health(conn=Depends(_db_dep)):
    rows = conn.execute(
        "SELECT name, ats, fetched, kept, status, error, last_run "
        "FROM source_health ORDER BY status DESC, fetched DESC"
    ).fetchall()
    return [dict(r) for r in rows]



@app.get("/api/counts")
def counts(conn=Depends(_db_dep)):
    threshold = int(_get_setting(conn, "score_threshold", 70))
    out = {
        "feed": conn.execute(f"SELECT COUNT(*) FROM jobs WHERE status='surfaced' AND score >= {threshold}").fetchone()[
            0],
        "saved": conn.execute("SELECT COUNT(*) FROM jobs WHERE status='saved'").fetchone()[0],
        "applied": conn.execute("SELECT COUNT(*) FROM jobs WHERE status='applied'").fetchone()[0],
        "dismissed": conn.execute("SELECT COUNT(*) FROM jobs WHERE status='dismissed'").fetchone()[0],
        "unscored": conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE score IS NULL AND status='surfaced'"
        ).fetchone()[0],
    }
    from src import followups
    out["followups"] = followups.summary(conn)["total"]
    return out


@app.get("/api/settings")
def get_settings(conn=Depends(_db_dep)):
    t = int(_get_setting(conn, "score_threshold", 70))
    return {"score_threshold": t}


class ThresholdUpdate(BaseModel):
    value: int


@app.post("/api/settings/threshold")
def set_threshold(body: ThresholdUpdate, conn=Depends(_db_dep)):
    v = max(0, min(100, body.value))
    conn.execute("INSERT INTO settings (key,value) VALUES ('score_threshold',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(v),))
    conn.commit()
    return {"score_threshold": v}


@app.get("/api/stats")
def stats(conn=Depends(_db_dep)):
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
def list_jobs(tab: str = "feed", sort: str = "score", source: str = "all", conn=Depends(_db_dep)):
    threshold = int(_get_setting(conn, "score_threshold", 70))

    # An unknown tab used to fall back to the feed, which quietly showed the wrong
    # list instead of signalling the mistake. A tab the app does not define returns
    # nothing — a visibly empty list is a clearer "that is not a real tab" than a
    # screenful of feed under the wrong heading.
    if tab == "feed":
        where = f"status='surfaced' AND score IS NOT NULL AND score >= {threshold}"
    elif tab in TAB_WHERE:
        where = TAB_WHERE[tab]
    else:
        where = "1 = 0"          # no such tab -> no rows

    params = []
    if source and source != "all":
        where += " AND source = ?"
        params.append(source)

    order = {"score": "score DESC", "newest": "posted_date DESC",
             "company": "company ASC"}.get(sort, "score DESC")   # whitelist, safe
    if tab == "unscored" and sort == "score":
        order = "id DESC"          # nothing to rank by; show the newest first

    rows = conn.execute(f"SELECT {COLS} FROM jobs WHERE {where} ORDER BY {order}",
                        params).fetchall()
    return [dict(r) for r in rows]

@app.get("/api/sources")
def sources_list(conn=Depends(_db_dep)):
    rows = conn.execute("SELECT DISTINCT source FROM jobs ORDER BY source").fetchall()
    return [r["source"] for r in rows if r["source"]]


@app.get("/api/errors")
def errors_list(limit: int = 100, conn=Depends(_db_dep)):
    """Everything that has gone wrong, newest first."""
    rows = store.recent_errors(conn, limit)
    return rows


@app.post("/api/errors/clear")
def errors_clear(conn=Depends(_db_dep)):
    n = store.clear_errors(conn)
    return {"cleared": n}


@app.get("/api/runs")
def runs_list(limit: int = 50, conn=Depends(_db_dep)):
    """The fetch history — what each run pulled in and kept."""
    rows = store.recent_runs(conn, limit)
    return rows


@app.on_event("startup")
def _startup():
    # Bring the database up to date before anything can query it.
    #
    # Every schema change used to need `python data/init_db.py` run by hand, and
    # forgetting it did not produce a helpful message — it produced a 500 from
    # deep inside a query, on whichever endpoint happened to touch the new column
    # first. The app appeared to be broken rather than out of date.
    #
    # The migration is idempotent and takes milliseconds on an up-to-date
    # database, so there is no reason not to simply do it. `init_db.py` still
    # exists for a fresh clone.
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from data.init_db import main as migrate
        migrate()
    except Exception as e:
        # Never take the server down over this — a failed migration should be
        # loud, not fatal. The endpoints that need the new columns will fail
        # clearly, and the reason is right here in the log.
        log.error("[startup] schema migration failed: %s", e)

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
def get_schedule(conn=Depends(_db_dep)):
    enabled = _get_setting(conn, "scheduler_enabled", "1") == "1"
    hours = float(_get_setting(conn, "run_interval_hours", "8") or 8)
    s = scheduler.get_state()
    return {"enabled": enabled, "interval_hours": hours,
            "last_run": s["last_run"], "next_run": s["next_run"], "running": s["running"]}


class ScheduleUpdate(BaseModel):
    enabled: bool
    interval_hours: float


@app.post("/api/schedule")
def set_schedule(body: ScheduleUpdate, conn=Depends(_db_dep)):
    hours = max(0.5, min(168.0, body.interval_hours))
    for k, v in (("scheduler_enabled", "1" if body.enabled else "0"),
                 ("run_interval_hours", str(hours))):
        conn.execute("INSERT INTO settings (key,value) VALUES (?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    conn.commit()
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


@app.post("/api/sources")
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
def set_status(job_id: int, body: StatusUpdate, conn=Depends(_db_dep)):
    if body.status not in ALLOWED_STATUS:
        raise HTTPException(400, f"invalid status: {body.status}")
    if body.status == "applied":
        cur = conn.execute(
            "UPDATE jobs SET status=?, applied_on=COALESCE(applied_on, date('now')) WHERE id=?",
            (body.status, job_id),
        )
    else:
        cur = conn.execute("UPDATE jobs SET status=? WHERE id=?", (body.status, job_id))
    conn.commit()
    changed = cur.rowcount
    if not changed:
        raise HTTPException(404, "job not found")
    return {"id": job_id, "status": body.status}


class NotesUpdate(BaseModel):
    notes: str

@app.post("/api/jobs/{job_id}/notes")
def set_notes(job_id: int, body: NotesUpdate, conn=Depends(_db_dep)):
    conn.execute("UPDATE jobs SET notes=? WHERE id=?", (body.notes, job_id))
    conn.commit()
    return {"ok": True}

@app.get("/api/runs")
def list_runs(limit: int = 20, conn=Depends(_db_dep)):
    rows = conn.execute(
        "SELECT id, started_at, kind, fetched, seen, dropped, trashed, kept, errors "
        "FROM runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]

@app.get("/api/model")
def model_state():
    from src.scoring.rerank import get_model_state
    return get_model_state()

class ModelUpdate(BaseModel):
    model: str


@app.post("/api/model")
def set_model(body: ModelUpdate, conn=Depends(_db_dep)):
    from src.scoring.rerank import set_preferred, get_model_state
    set_preferred(body.model)
    conn.execute("INSERT INTO settings (key,value) VALUES ('scoring_model',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (body.model,))
    conn.commit()
    return get_model_state()

@app.get("/api/notify")
def get_notify(conn=Depends(_db_dep)):
    from src import notify
    enabled = _get_setting(conn, "notify_enabled", "1") == "1"
    return {"enabled": enabled, "configured": bool(notify._token() and notify._chat_id())}


class NotifyUpdate(BaseModel):
    enabled: bool


@app.post("/api/notify")
def set_notify(body: NotifyUpdate, conn=Depends(_db_dep)):
    conn.execute("INSERT INTO settings (key,value) VALUES ('notify_enabled',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 ("1" if body.enabled else "0",))
    conn.commit()
    return {"enabled": body.enabled}


@app.post("/api/notify/test")
def test_notify():
    from src import notify
    ok = notify.send("JobPilot test — notifications working ✅")
    return {"sent": ok}

# ── AI features (scrape-time scoring / on-demand generation) ────────────────

@app.get("/api/ai-features")
def get_ai_features(conn=Depends(_db_dep)):
    scoring = _get_setting(conn, "scoring_enabled", "1") == "1"
    generation = _get_setting(conn, "generation_enabled", "1") == "1"
    return {"scoring": scoring, "generation": generation}


class AIFeature(BaseModel):
    feature: str          # "scoring" | "generation"
    enabled: bool


@app.post("/api/ai-features")
def set_ai_features(body: AIFeature, conn=Depends(_db_dep)):
    keys = {"scoring": "scoring_enabled", "generation": "generation_enabled"}
    if body.feature not in keys:
        raise HTTPException(400, "unknown feature")
    conn.execute("INSERT INTO settings (key,value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (keys[body.feature], "1" if body.enabled else "0"))
    conn.commit()
    return {"feature": body.feature, "enabled": body.enabled}


# ── Connection tests ────────────────────────────────────────────────────────

@app.post("/api/llm/test")
def llm_test():
    """Send a tiny prompt through the provider chain to verify it works."""
    from src import llm
    try:
        text, provider = llm.generate(
            "You are a connection test. Reply with exactly: OK",
            "Reply with exactly: OK",
        )
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"ok": True, "provider": provider, "reply": text[:80]}


# ── Configuration files ─────────────────────────────────────────────────────

@app.get("/api/config/files")
def config_files():
    """Paths of the files the user edits, plus whether each one exists."""
    from src.paths import CONFIG_FILES, ROOT
    out = []
    for f in CONFIG_FILES:
        out.append({**f, "exists": (ROOT / f["path"]).exists()})
    return {"files": out, "root": str(ROOT)}


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


def _generation_http_error(e: Exception) -> HTTPException:
    """Turn a generation failure into a message a person can act on.

    The most common failure by far is that no AI provider is configured — a new user
    with no API key and no local Ollama. The raw exception for that is
    "all providers failed -> gemini: not configured | cerebras: not configured |
    ollama: ...", which is accurate and useless to the person reading it. Catch that
    one case and say what to do about it; anything genuinely unexpected still gets the
    502 with its type, and the full traceback is already on the console.
    """
    from src.llm import LLMError

    msg = str(e)
    if isinstance(e, LLMError) or "all providers failed" in msg:
        return HTTPException(503, (
            "No AI provider is available. Add a Gemini or Cerebras API key to your "
            ".env, or run Ollama locally, then try again."))
    return HTTPException(502, f"{type(e).__name__}: {e}")


@app.post("/api/jobs/{job_id}/cover-letter")
@limiter.limit(RATE_LIMIT_GENERATION)
def cover_letter(request: Request, job_id: int, conn=Depends(_db_dep)):
    """Generate a grounded cover letter for one job."""
    if _get_setting(conn, "generation_enabled", "1") != "1":
        raise HTTPException(
            403, "On-demand AI is off — enable it in Settings > AI features."
        )
    row = conn.execute(
        "SELECT title, company, description FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "job not found")
    try:
        from src import apply          # imported here so import errors surface
        result = apply.generate_cover_letter(dict(row))
    except resume_guard.FabricationError as e:
        # The letter named something the profile does not contain. It was written and
        # then refused — the same fatal stance the resume takes. Do not hand it over.
        raise HTTPException(422, {"error": "fabricated",
                                  "problems": e.problems,
                                  "message": str(e)})
    except resume_guard.ProfileIncompleteError as e:
        raise HTTPException(400, {"error": "profile_incomplete",
                                  "missing": e.missing,
                                  "message": str(e)})
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()          # full trace in the uvicorn console
        raise _generation_http_error(e)
    return result


@app.post("/api/jobs/{job_id}/resume")
@limiter.limit(RATE_LIMIT_GENERATION)
def tailored_resume(request: Request, job_id: int, conn=Depends(_db_dep)):
    """Tailor the resume template to one job."""
    if _get_setting(conn, "generation_enabled", "1") != "1":
        raise HTTPException(
            403, "On-demand AI is off — enable it in Settings > AI features."
        )
    row = conn.execute(
        "SELECT title, company, description FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "job not found")
    try:
        from src import apply
        result = apply.generate_resume(dict(row))
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))
    except resume_fit.JobDoesNotFitError as e:
        # Nothing was generated, and nothing should have been.
        raise HTTPException(422, {"error": "does_not_fit",
                                  "score": round(e.score * 100),
                                  "matched": sorted(e.matched)[:8],
                                  "message": str(e)})
    except resume_guard.ProfileIncompleteError as e:
        # Nothing was generated. Say exactly what is missing.
        raise HTTPException(400, {"error": "profile_incomplete",
                                  "missing": e.missing,
                                  "message": str(e)})
    except resume_guard.FabricationError as e:
        # Something was generated and then refused. Do not hand it over.
        raise HTTPException(422, {"error": "fabricated",
                                  "problems": e.problems,
                                  "message": str(e)})
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise _generation_http_error(e)
    return result


# ── Autofill (browser extension) ────────────────────────────────────────────

@app.get("/api/autofill/data")
def autofill_data():
    """Canonical answers plus the user's own custom rules — no AI, instant."""
    from src import autofill
    return {"answers": autofill.answers(),
            "custom": autofill.custom_answers()}


class ResolveField(BaseModel):
    id: str
    label: str = ""
    type: str = "text"
    options: list[str] = []


class ResolveRequest(BaseModel):
    fields: list[ResolveField]
    job_id: int | None = None


@app.post("/api/autofill/resolve")
def autofill_resolve(body: ResolveRequest, conn=Depends(_db_dep)):
    """AI-map the fields local heuristics couldn't place. Blank if unknown."""
    if _get_setting(conn, "generation_enabled", "1") != "1":
        raise HTTPException(403, "On-demand AI is off — enable it in Settings.")

    job = None
    if body.job_id:
        row = conn.execute(
            "SELECT title, company FROM jobs WHERE id=?", (body.job_id,)
        ).fetchone()
        job = dict(row) if row else None

    try:
        from src import autofill
        mapped = autofill.resolve([f.model_dump() for f in body.fields], job)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(502, f"{type(e).__name__}: {e}")
    return {"answers": mapped}


# ── Materials (generated documents, bound to a job) ─────────────────────────

class MaterialSave(BaseModel):
    kind: str                 # "resume" | "cover"
    content: str
    provider: str = ""


@app.post("/api/jobs/{job_id}/materials")
def save_material(job_id: int, body: MaterialSave, conn=Depends(_db_dep)):
    """Store a generated document against this job."""
    from src import materials
    exists = conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not exists:
        raise HTTPException(404, "job not found")
    try:
        return materials.save(job_id, body.kind, body.content, body.provider)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/jobs/{job_id}/materials")
def list_materials(job_id: int):
    """What has been saved for this job (kinds + timestamps, not the bodies)."""
    from src import materials
    return {"job_id": job_id, "materials": materials.list_for(job_id)}


@app.delete("/api/jobs/{job_id}/materials/{kind}")
def delete_material(job_id: int, kind: str):
    from src import materials
    return {"deleted": materials.delete(job_id, kind)}


@app.get("/api/jobs/{job_id}/materials/{kind}/file")
def material_file(job_id: int, kind: str, format: str = "pdf", conn=Depends(_db_dep)):
    """Download a saved document. This is what the extension attaches.

    The document is looked up by job_id, so the file returned always belongs to
    the job it is requested for — there is no way to serve one company's letter
    for another company's application.
    """
    from src import materials

    job = conn.execute(
        "SELECT id, title, company FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    if not job:
        raise HTTPException(404, "job not found")

    doc = materials.get(job_id, kind)
    if not doc:
        raise HTTPException(
            404, f"no {kind} saved for this job — generate and save it first"
        )

    job = dict(job)
    if format == "docx":
        # Word, for the resume. Most ATS parse .docx at least as well as PDF, and
        # several parse it better.
        try:
            data = materials.to_docx(doc["content"], kind)
        except (RuntimeError, ValueError) as e:
            raise HTTPException(400, str(e))
        media = ("application/vnd.openxmlformats-officedocument"
                 ".wordprocessingml.document")
        ext = "docx"
    elif format == "pdf":
        try:
            data = materials.to_pdf(doc["content"], kind)
        except RuntimeError as e:
            raise HTTPException(500, str(e))
        media = "application/pdf"
        ext = "pdf"
    else:
        data = doc["content"].encode("utf-8")
        media = "text/plain; charset=utf-8"
        ext = "md" if kind == "resume" else "txt"

    name = materials.filename(job, kind, ext)
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# ── Matching a browser page to a job ────────────────────────────────────────

def _normalize_url(url: str) -> str:
    """Host + path, lowercased, no scheme/query/fragment/trailing slash.

    Application URLs pick up tracking parameters and vary between http/https and
    with/without www, but the host+path is stable — that is what we compare.
    """
    if not url:
        return ""
    url = re.sub(r"^https?://", "", url.strip().lower())
    url = url.split("?")[0].split("#")[0]
    url = re.sub(r"^www\.", "", url)
    return url.rstrip("/")


@app.get("/api/jobs/match")
def match_job(url: str, conn=Depends(_db_dep)):
    """Find the job this browser page belongs to.

    Confidence is deliberately conservative: a wrong match would attach the wrong
    company's cover letter, which is far worse than attaching nothing. Anything
    below an exact host+path match is returned as a *suggestion* for the user to
    confirm, never as an automatic binding.
    """
    target = _normalize_url(url)
    if not target:
        return {"match": None, "candidates": []}

    rows = conn.execute(
        "SELECT id, title, company, apply_url, source_url, status FROM jobs "
        "WHERE apply_url IS NOT NULL OR source_url IS NOT NULL"
    ).fetchall()

    exact, partial = [], []
    for r in rows:
        job = dict(r)
        for field in ("apply_url", "source_url"):
            stored = _normalize_url(job.get(field) or "")
            if not stored:
                continue
            if stored == target:
                exact.append(job)
                break
            # The ATS often redirects to a longer path (…/apply, …/application).
            if target.startswith(stored + "/") or stored.startswith(target + "/"):
                partial.append(job)
                break

    def slim(j):
        return {"id": j["id"], "title": j["title"],
                "company": j["company"], "status": j["status"]}

    if len(exact) == 1:
        return {"match": slim(exact[0]), "confidence": "exact", "candidates": []}
    if not exact and len(partial) == 1:
        return {"match": slim(partial[0]), "confidence": "path", "candidates": []}

    # Ambiguous or nothing found — let the user choose rather than guessing.
    candidates = [slim(j) for j in (exact + partial)][:10]
    return {"match": None, "confidence": "none", "candidates": candidates}


@app.get("/api/jobs/search")
def search_jobs(q: str = "", limit: int = 10, conn=Depends(_db_dep)):
    """Free-text search over title/company, for the extension's manual picker."""
    like = f"%{q.strip()}%"
    rows = conn.execute(
        "SELECT id, title, company, status FROM jobs "
        "WHERE title LIKE ? OR company LIKE ? "
        "ORDER BY CASE status WHEN 'saved' THEN 0 WHEN 'applied' THEN 1 ELSE 2 END, "
        "score DESC LIMIT ?",
        (like, like, limit),
    ).fetchall()
    return [dict(r) for r in rows]


# ── Importing jobs from outside the fetch pipeline ──────────────────────────

async def _read_capped(file) -> bytes:
    """Read an upload, refusing anything over the cap.

    UploadFile.read() with no argument pulls the entire body into memory, so the size
    limit has to be enforced here — a client that sends a 2 GB file should get a 413,
    not an out-of-memory kill. Read in chunks and stop the moment the cap is passed,
    so an oversized upload is rejected without ever being fully buffered.
    """
    chunks = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)     # 1 MB at a time
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                413, f"file too large — the limit is "
                     f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        chunks.append(chunk)
    return b"".join(chunks)


@app.post("/api/import/file")
@limiter.limit(RATE_LIMIT_IMPORT)
async def import_file(request: Request, file: UploadFile = File(...)):
    """Import jobs from a CSV or Excel file."""
    from src import importers
    data = await _read_capped(file)
    try:
        rows = importers.parse_tabular(data, file.filename or "")
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"couldn't read that file: {e}")

    if not rows:
        raise HTTPException(
            400, "no usable rows — the file needs at least a title and a company column"
        )
    stats = importers.import_jobs(rows, source="import")
    return {"rows": len(rows), **stats}


class PastedJob(BaseModel):
    text: str


@app.post("/api/import/text")
@limiter.limit(RATE_LIMIT_IMPORT)
def import_text(request: Request, body: PastedJob, conn=Depends(_db_dep)):
    """Paste a whole job posting; the model pulls the fields out of it."""
    if _get_setting(conn, "generation_enabled", "1") != "1":
        raise HTTPException(403, "On-demand AI is off — enable it in Settings.")

    from src import importers
    try:
        job = importers.parse_text(body.text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(502, f"{type(e).__name__}: {e}")

    stats = importers.import_jobs([job], source="pasted", fetch_missing=False)
    return {"job": {"title": job["title"], "company": job["company"]}, **stats}


@app.post("/api/import/email-file")
@limiter.limit(RATE_LIMIT_IMPORT)
async def import_email_file(request: Request, file: UploadFile = File(...)):
    """Import jobs from a job-alert email you exported (.eml or .html).

    JobPilot has no mail credentials and no IMAP client. It reads the file you
    hand it and nothing else — there is no path from this app to your mailbox.
    """
    from src import importers
    data = await _read_capped(file)
    try:
        jobs = importers.parse_email_file(data, file.filename or "")
    except Exception as e:
        raise HTTPException(400, f"couldn't read that email: {e}")

    if not jobs:
        raise HTTPException(
            400, "no job links found in that email — is it a job-alert email?"
        )
    stats = importers.import_jobs(jobs)
    return {"found": len(jobs), **stats}


@app.post("/api/import/mail-drop")
def import_mail_drop():
    """Ingest every alert email sitting in data/mail_drop/.

    Drag your exported emails in there and press the button. Files are read and
    left alone.
    """
    from src import importers
    try:
        jobs, files = importers.read_mail_drop()
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(502, f"{type(e).__name__}: {e}")

    if not jobs:
        return {"files": len(files), "found": 0, "seen": 0, "imported": 0,
                "scored": 0, "unscored": 0, "duplicates": 0, "errors": 0}

    stats = importers.import_jobs(jobs)
    return {"files": len(files), "found": len(jobs), **stats}


# ── Privacy ─────────────────────────────────────────────────────────────────

@app.get("/api/privacy")
def get_privacy():
    from src import llm, importers
    from src.paths import PRIVACY_MODE
    return {"mode": llm.privacy_mode(),
            "default": PRIVACY_MODE,
            "follow_job_links": importers.follow_links_enabled()}


class PrivacyUpdate(BaseModel):
    mode: str | None = None                 # "redacted" | "local" | "full"
    follow_job_links: bool | None = None


@app.post("/api/privacy")
def set_privacy(body: PrivacyUpdate, conn=Depends(_db_dep)):
    if body.mode is not None:
        if body.mode not in ("redacted", "local", "full"):
            raise HTTPException(400, f"unknown privacy mode: {body.mode}")
        conn.execute("INSERT INTO settings (key,value) VALUES ('privacy_mode',?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (body.mode,))
    if body.follow_job_links is not None:
        conn.execute("INSERT INTO settings (key,value) VALUES ('follow_job_links',?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     ("1" if body.follow_job_links else "0",))
    conn.commit()

    from src import llm, importers
    return {"mode": llm.privacy_mode(),
            "follow_job_links": importers.follow_links_enabled()}


@app.get("/api/import/template")
def import_template():
    """A starter CSV with the columns the importer understands."""
    header = "title,company,location,apply_url,description,posted_date,job_type,salary\n"
    example = ('Junior Backend Developer,Shopify,"Toronto, Canada",'
               'https://example.com/jobs/1,"We are looking for...",2026-07-01,Full-time,\n')
    return Response(
        content=header + example,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="jobpilot_import_template.csv"'},
    )


# ── Feedback loop ───────────────────────────────────────────────────────────

@app.get("/api/feedback")
def get_feedback(conn=Depends(_db_dep)):
    """What the scoring has learned from your save/dismiss decisions."""
    from src.scoring import feedback
    from src.scoring.rerank import scoring_via_chain
    data = feedback.stats(conn)
    data["scoring_via_chain"] = scoring_via_chain()
    return data


class ScoringUpdate(BaseModel):
    scoring_via_chain: bool


@app.post("/api/feedback/scoring")
def set_scoring_chain(body: ScoringUpdate, conn=Depends(_db_dep)):
    """Score through the provider chain, or pin scoring to local Ollama."""
    conn.execute("INSERT INTO settings (key,value) VALUES ('scoring_via_chain',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 ("1" if body.scoring_via_chain else "0",))
    conn.commit()
    return {"scoring_via_chain": body.scoring_via_chain}


# ── Follow-ups ──────────────────────────────────────────────────────────────

@app.get("/api/followups")
def list_followups(conn=Depends(_db_dep)):
    """Applications that need a nudge today."""
    from src import followups
    items = followups.due(conn)
    counts = followups.summary(conn)
    return {"items": items, **counts}


class FollowupAction(BaseModel):
    action: str                # "done" | "snooze"
    days: int = 7              # for snooze


@app.post("/api/jobs/{job_id}/followup")
def set_followup(job_id: int, body: FollowupAction, conn=Depends(_db_dep)):
    from src import followups
    if body.action == "done":
        ok = followups.mark_followed_up(conn, job_id)
    elif body.action == "snooze":
        ok = followups.snooze(conn, job_id, body.days)
    else:
        raise HTTPException(400, f"unknown action: {body.action}")

    if not ok:
        raise HTTPException(404, "job not found, or it isn't an applied job")
    return {"id": job_id, "action": body.action}


# ── Source health ───────────────────────────────────────────────────────────

@app.get("/api/health/assess")
def assess_health(conn=Depends(_db_dep)):
    """Every board with a verdict — including the ones failing silently."""
    from src import health
    boards = health.assess(conn)
    counts = health.summary(conn)
    return {"boards": boards, **counts}


@app.post("/api/notify/test-digest")
def send_test_digest(conn=Depends(_db_dep)):
    """Send this week's digest now, so you can see what it looks like."""
    from src import health, notify
    stats = health.week_stats(conn)

    message = notify.weekly_digest(stats)
    if not notify.enabled():
        return {"sent": False, "preview": message,
                "reason": "Telegram isn't configured, or notifications are off."}
    return {"sent": notify.send(message), "preview": message}


app.mount("/", StaticFiles(
    directory=str(Path(__file__).parent.parent / "frontend"),
    html=True,
), name="frontend")
