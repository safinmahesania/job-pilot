"""FastAPI backend for JobPilot — serves jobs, status updates, and the frontend."""
import sqlite3
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import threading
from datetime import datetime
from fastapi.staticfiles import StaticFiles

DB = "jobpilot.db"
app = FastAPI(title="JobPilot")

COLS = ("id, title, company, location, remote, job_type, source, source_url, "
        "apply_url, description, posted_date, deadline, score, skills_score, "
        "seniority_score, domain_score, rationale, status")

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


class StatusUpdate(BaseModel):
    status: str


@app.post("/api/jobs/{job_id}/status")
def update_status(job_id: int, body: StatusUpdate):
    if body.status not in ALLOWED_STATUS:
        raise HTTPException(400, f"invalid status: {body.status}")
    conn = _conn()
    cur = conn.execute("UPDATE jobs SET status = ? WHERE id = ?",
                       (body.status, job_id))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    if not changed:
        raise HTTPException(404, "job not found")
    return {"id": job_id, "status": body.status}


# ---- pipeline run state (in-memory) ----
_run_state = {"running": False, "last_run": None, "last_summary": None}


def _run_pipeline():
    from src.run import run as run_pipeline
    _run_state["running"] = True
    try:
        run_pipeline()
        _run_state["last_summary"] = "completed"
    except Exception as e:
        _run_state["last_summary"] = f"error: {e}"
    finally:
        _run_state["running"] = False
        _run_state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M")


@app.post("/api/run")
def trigger_run():
    if _run_state["running"]:
        raise HTTPException(409, "pipeline already running")
    threading.Thread(target=_run_pipeline, daemon=True).start()
    return {"started": True}


@app.get("/api/run/status")
def run_status():
    return _run_state


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


@app.get("/api/jobs")
def list_jobs(tab: str = "feed"):
    conn = _conn()
    threshold = int(_get_setting(conn, "score_threshold", 70))
    if tab == "feed":
        where = f"status='surfaced' AND score >= {threshold}"
    else:
        where = TAB_WHERE.get(tab, TAB_WHERE["feed"])
    rows = conn.execute(f"SELECT {COLS} FROM jobs WHERE {where} ORDER BY score DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


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


app.mount("/", StaticFiles(
    directory=str(Path(__file__).parent.parent / "frontend"),
    html=True,
), name="frontend")