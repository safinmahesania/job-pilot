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
    scoring_on = _get_setting(conn, "scoring_enabled", "1") == "1"

    # An unknown tab used to fall back to the feed, which quietly showed the wrong
    # list instead of signalling the mistake. A tab the app does not define returns
    # nothing — a visibly empty list is a clearer "that is not a real tab" than a
    # screenful of feed under the wrong heading.
    if tab == "feed":
        if scoring_on:
            # Normal case: the feed is the ranked shortlist — scored jobs at or above
            # the threshold.
            where = f"status='surfaced' AND score IS NOT NULL AND score >= {threshold}"
        else:
            # Scoring is turned off, so nothing gets a score and the threshold filter
            # would hide every surfaced job — the feed would look empty even though the
            # jobs are right there. With no scores to rank by, show all surfaced jobs
            # instead of an empty page.
            where = "status = 'surfaced'"
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
    if sort == "score" and (tab == "unscored" or (tab == "feed" and not scoring_on)):
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


# ── Manual edit (fix a job the fetcher got wrong) ──

# Only the descriptive fields a person would correct by hand. The pipeline's own
# bookkeeping — id, dedupe_hash, source, score and the rest — is deliberately not here:
# editing those would either corrupt de-duplication or silently fake a score the model
# never gave. Everything a bad scrape actually gets wrong (a truncated description, a
# title that grabbed the wrong line, a missing apply link) is editable.
EDITABLE_FIELDS = {
    "title": str,
    "company": str,
    "location": str,
    "description": str,
    "apply_url": str,
    "source_url": str,
    "job_type": str,
    "posted_date": str,
    "deadline": str,
    "remote": int,           # 0 / 1
    "salary_min": int,
    "salary_max": int,
}


class JobEdit(BaseModel):
    title: str | None = None
    company: str | None = None
    location: str | None = None
    description: str | None = None
    apply_url: str | None = None
    source_url: str | None = None
    job_type: str | None = None
    posted_date: str | None = None
    deadline: str | None = None
    remote: int | None = None
    salary_min: int | None = None
    salary_max: int | None = None


@router.patch("/api/jobs/{job_id}")
def edit_job(job_id: int, body: JobEdit, defer: bool = False,
              conn=Depends(_db_dep)):
    """Correct a job's fields by hand — for when a fetch grabbed the wrong text or
    only half a description. Only the fields you actually send are changed; anything
    left out keeps its current value."""
    exists = conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not exists:
        raise HTTPException(404, "job not found")

    # Only the fields the caller explicitly set — model_dump(exclude_unset) keeps this
    # a true partial edit, so sending {"title": "..."} touches the title and nothing
    # else. Every key is checked against the whitelist, so the column list can never
    # be steered from the request body.
    changes = body.model_dump(exclude_unset=True)
    changes = {k: v for k, v in changes.items() if k in EDITABLE_FIELDS}
    if not changes:
        raise HTTPException(400, "no editable fields provided")

    assignments = ", ".join(f"{col}=?" for col in changes)   # col names from whitelist
    values = list(changes.values())
    values.append(job_id)
    conn.execute(f"UPDATE jobs SET {assignments} WHERE id=?", values)
    conn.commit()

    # Re-score right away if the edit touched something the score actually depends on.
    # A fixed title or a description that was half-fetched changes the match; a
    # corrected apply link or a date does not, so those don't trigger the model. This
    # is best-effort: if scoring is off, or every provider is down, the edit still
    # stands — the job just keeps its old score (or stays unscored) rather than the
    # correction being lost because the model wasn't reachable.
    #
    # `defer=true` hands that job back to the caller. The UI uses it so it can run the
    # steps one at a time and name each one while it happens — and so it can skip
    # scoring entirely when the filters have already removed the job. Callers that
    # don't ask for it keep the original behaviour.
    rescored = None
    deferred = False
    SCORING_FIELDS = {"title", "company", "description"}
    scoring_on = _get_setting(conn, "scoring_enabled", "1") == "1"
    if scoring_on and (set(changes) & SCORING_FIELDS):
        if defer:
            deferred = True
        else:
            rescored = _rescore_one(conn, job_id)

    row = conn.execute(
        "SELECT id, title, company, location, description, apply_url, source_url, "
        "job_type, posted_date, deadline, remote, salary_min, salary_max, score "
        "FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    return {"updated": list(changes), "rescored": rescored,
            "needs_reprocess": deferred, "job": dict(row)}


@router.post("/api/jobs/{job_id}/recheck")
def recheck_job(job_id: int, conn=Depends(_db_dep)):
    """Put an edited job back through the filters a fetched job goes through.

    A job that arrived with half a description was judged on half a description. Once
    you paste the real posting, the things that were unknowable become knowable — that
    it is in Austin, that it is a staff role, that it names a keyword you exclude — and
    a job that looked fine on its title turns out not to be one you want.

    A fetch run drops such a job without ceremony. This does the same, and says which
    rule did it: you corrected this job by hand, so you should be told what happened to
    it rather than watch it vanish.

    Scoring is deliberately NOT done here. It is the slow half, and there is no point
    paying for it on a job the filters just removed.
    """
    row = conn.execute(
        "SELECT id, title, company, location, description, job_type, remote, "
        "salary_min, salary_max, posted_date, source_url, apply_url, status "
        "FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "job not found")
    job = dict(row)

    from src.config import load_profile
    from src.scoring.prefilter import why_not

    profile = load_profile() or {}
    if not profile:
        # No profile means no constraints to check against. Saying "passed" would be a
        # lie; the honest answer is that nothing was checked.
        return {"verdict": "unchecked",
                "reason": "no profile.yaml, so there were no filters to apply"}

    reason = why_not(job, profile)
    if reason is None:
        return {"verdict": "ok", "reason": None}

    conn.execute("UPDATE jobs SET status='dismissed' WHERE id=?", (job_id,))
    conn.commit()
    return {"verdict": "dismissed", "reason": reason}


def _rescore_one(conn, job_id: int):
    """Score a single edited job now, and persist the result. Returns the new score,
    or None if scoring couldn't run (no model available, profile missing, etc.) — in
    which case the job's existing score is left untouched and the edit is unaffected."""
    try:
        from src import configio
        from src.scoring.rerank import score_job, build_calibration

        profile = configio.read_yaml("profile.yaml") or {}
        if not profile:
            return None

        job = conn.execute(
            "SELECT title, company, location, description, job_type FROM jobs WHERE id=?",
            (job_id,)
        ).fetchone()
        if not job:
            return None

        calibration = build_calibration()
        result = score_job(dict(job), profile, calibration)
        if result is None:
            return None

        conn.execute(
            "UPDATE jobs SET score=?, skills_score=?, seniority_score=?, "
            "domain_score=?, rationale=? WHERE id=?",
            (result.overall, result.skills_score, result.seniority_score,
             result.domain_score, result.rationale, job_id),
        )
        conn.commit()
        return round(result.overall)
    except Exception:
        import traceback
        traceback.print_exc()          # full trace to the console; the edit still stands
        return None


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
