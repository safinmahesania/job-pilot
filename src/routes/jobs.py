"""The jobs themselves: the feed, counts, stats, and per-job actions.

This is the core read surface — the tabbed list the UI shows, the tab/funnel counts, and
the stats dashboard — plus the actions that move a job through the pipeline (mark it
saved/applied/dismissed, attach a note) and the two lookups the browser extension uses to
find which stored job a page belongs to.
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.deps import _db_dep, _get_setting, COLS, TAB_WHERE, ALLOWED_STATUS

router = APIRouter()


@router.get("/api/counts")
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


@router.get("/api/stats")
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

    # source breakdown (from the jobs table: avg score + count per source)
    src_rows = conn.execute(
        "SELECT source, COUNT(*) c, ROUND(AVG(score),1) avg FROM jobs "
        "GROUP BY source ORDER BY c DESC"
    ).fetchall()
    sources = [dict(r) for r in src_rows]

    # deadlines — next 14 days / expired (deadline is text, so date-compare is best-effort)
    deadline_rows = conn.execute(
        "SELECT title, company, deadline FROM jobs "
        "WHERE deadline IS NOT NULL AND deadline != '' ORDER BY deadline ASC LIMIT 10"
    ).fetchall()
    deadlines = [dict(r) for r in deadline_rows]

    # conversion rates
    def pct(a, b): return round(100 * a / b, 1) if b else 0

    applied = funnel["applied"] + funnel["interview"] + funnel["offer"]  # applied and beyond
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


@router.get("/api/jobs")
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


class StatusUpdate(BaseModel):
    status: str


@router.post("/api/jobs/{job_id}/status")
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


@router.post("/api/jobs/{job_id}/notes")
def set_notes(job_id: int, body: NotesUpdate, conn=Depends(_db_dep)):
    conn.execute("UPDATE jobs SET notes=? WHERE id=?", (body.notes, job_id))
    conn.commit()
    return {"ok": True}


# ── Matching a browser page to a stored job (used by the extension) ──

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


@router.get("/api/jobs/match")
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


@router.get("/api/jobs/search")
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