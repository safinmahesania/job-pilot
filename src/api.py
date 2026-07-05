"""FastAPI backend for JobPilot — serves jobs, status updates, and the frontend."""
import sqlite3
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

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
}

ALLOWED_STATUS = {"surfaced", "saved", "applied", "dismissed",
                  "interview", "offer", "rejected"}


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


@app.get("/api/jobs")
def list_jobs(tab: str = "feed"):
    where = TAB_WHERE.get(tab, TAB_WHERE["feed"])
    conn = _conn()
    rows = conn.execute(
        f"SELECT {COLS} FROM jobs WHERE {where} ORDER BY score DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/counts")
def counts():
    conn = _conn()
    out = {tab: conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {w}").fetchone()[0]
           for tab, w in TAB_WHERE.items()}
    conn.close()
    return out


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


# ---- frontend (agle step me banega) ----
FRONTEND = Path(__file__).parent.parent / "frontend" / "index.html"


@app.get("/")
def index():
    if FRONTEND.exists():
        return FileResponse(FRONTEND)
    return {"msg": "JobPilot API running. Frontend abhi banana baaki hai."}