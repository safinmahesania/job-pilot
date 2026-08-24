"""The jobs themselves: the feed, counts, stats, and per-job actions.

This is the core read surface — the tabbed list the UI shows, the tab/funnel counts, and
the stats dashboard — plus the actions that move a job through the pipeline (mark it
saved/applied/dismissed, attach a note) and the two lookups the browser extension uses to
find which stored job a page belongs to.
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.auth import current_user_id
from src.deps import (_db_dep, _get_setting, _user_threshold, FEED_COLS,
                      TAB_WHERE, ALLOWED_STATUS)

router = APIRouter()


@router.get("/api/counts")
def counts(user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    threshold = _user_threshold(conn, user_id)

    def n(where: str) -> int:
        return conn.execute(
            f"SELECT COUNT(*) FROM user_jobs uj WHERE uj.user_id = ? AND {where}",
            (user_id,)).fetchone()[0]

    out = {
        "feed": n(f"uj.status='surfaced' AND uj.score >= {threshold}"),
        "saved": n("uj.status='saved'"),
        "applied": n("uj.status='applied'"),
        "dismissed": n("uj.status='dismissed'"),
        "unscored": n("uj.score IS NULL AND uj.status='surfaced'"),
    }
    from src import followups
    out["followups"] = followups.summary(conn, user_id)["total"]
    return out


@router.get("/api/stats")
def stats(user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    """A personal dashboard: every metric is over the jobs served to THIS user.

    Posting facts come from jobs (j.), the judgement — score, status, applied_on —
    from user_jobs (uj.). Everything joins the two, filtered to the caller.
    """
    from datetime import date, timedelta

    threshold = _user_threshold(conn, user_id)
    base = "FROM jobs j JOIN user_jobs uj ON uj.job_id = j.id WHERE uj.user_id = ?"

    def scalar(select: str, extra: str = "", *a):
        return conn.execute(f"SELECT {select} {base} {extra}",
                            (user_id, *a)).fetchone()[0]

    # funnel (per-user status counts)
    statuses = ["surfaced", "saved", "applied", "interview", "offer",
                "rejected", "dismissed"]
    funnel = {s: scalar("COUNT(*)", "AND uj.status=?", s) for s in statuses}

    total = scalar("COUNT(*)")
    avg_score = scalar("ROUND(AVG(uj.score)::numeric,1)") or 0
    feed_size = scalar("COUNT(*)",
                       f"AND uj.status='surfaced' AND uj.score>={threshold}")

    dist = {
        "80+": scalar("COUNT(*)", "AND uj.score>=80"),
        "70-79": scalar("COUNT(*)", "AND uj.score>=70 AND uj.score<80"),
        "60-69": scalar("COUNT(*)", "AND uj.score>=60 AND uj.score<70"),
        "<60": scalar("COUNT(*)", "AND uj.score<60"),
    }

    src_rows = conn.execute(
        f"SELECT j.source, COUNT(*) c, ROUND(AVG(uj.score)::numeric,1) avg {base} "
        "GROUP BY j.source ORDER BY c DESC", (user_id,)).fetchall()
    sources = [dict(r) for r in src_rows]

    deadline_rows = conn.execute(
        f"SELECT j.title, j.company, j.deadline {base} "
        "AND j.deadline IS NOT NULL AND j.deadline != '' "
        "ORDER BY j.deadline ASC LIMIT 10", (user_id,)).fetchall()
    deadlines = [dict(r) for r in deadline_rows]

    def pct(a, b): return round(100 * a / b, 1) if b else 0

    applied = funnel["applied"] + funnel["interview"] + funnel["offer"]
    rates = {
        "applied_of_total": pct(applied, total),
        "interview_of_applied": pct(funnel["interview"] + funnel["offer"], applied),
        "offer_of_interview": pct(funnel["offer"],
                                  funnel["interview"] + funnel["offer"]),
    }

    # Activity over the last 14 days: jobs served to me vs applications I sent.
    served_rows = conn.execute(
        f"SELECT (uj.served_at::date)::text d, COUNT(*) c {base} "
        "AND uj.served_at >= (CURRENT_DATE - 13) GROUP BY d", (user_id,)).fetchall()
    served_by_day = {r["d"]: r["c"] for r in served_rows}
    applied_rows = conn.execute(
        f"SELECT (uj.applied_on::date)::text d, COUNT(*) c {base} "
        "AND uj.applied_on IS NOT NULL AND uj.applied_on >= (CURRENT_DATE - 13) "
        "GROUP BY d", (user_id,)).fetchall()
    applied_by_day = {r["d"]: r["c"] for r in applied_rows}
    activity = []
    for i in range(13, -1, -1):
        day = (date.today() - timedelta(days=i)).isoformat()
        activity.append({"date": day, "fetched": served_by_day.get(day, 0),
                         "applied": applied_by_day.get(day, 0)})

    remote_ct = scalar("COUNT(*)", "AND j.remote")
    work_mix = {"remote": remote_ct, "onsite": total - remote_ct}
    type_rows = conn.execute(
        f"SELECT COALESCE(NULLIF(j.job_type,''),'unknown') t, COUNT(*) c {base} "
        "GROUP BY t ORDER BY c DESC", (user_id,)).fetchall()
    job_types = [dict(r) for r in type_rows]
    lang_rows = conn.execute(
        f"SELECT COALESCE(NULLIF(j.language,''),'unknown') l, COUNT(*) c {base} "
        "GROUP BY l ORDER BY c DESC", (user_id,)).fetchall()
    languages = [dict(r) for r in lang_rows]

    sal_count = scalar("COUNT(*)", "AND j.salary_min IS NOT NULL AND j.salary_min>0")
    salary = {
        "disclosed": sal_count,
        "disclosed_pct": pct(sal_count, total),
        "avg_min": scalar("ROUND(AVG(j.salary_min)::numeric)", "AND j.salary_min>0") or 0,
        "avg_max": scalar("ROUND(AVG(j.salary_max)::numeric)", "AND j.salary_max>0") or 0,
    }

    comp_rows = conn.execute(
        f"SELECT j.company, COUNT(*) c, ROUND(AVG(uj.score)::numeric,1) avg {base} "
        "AND j.company IS NOT NULL AND j.company != '' "
        "GROUP BY j.company ORDER BY c DESC LIMIT 8", (user_id,)).fetchall()
    companies = [dict(r) for r in comp_rows]

    score_parts = {
        "skills": scalar("ROUND(AVG(uj.skills_score)::numeric,1)",
                         "AND uj.skills_score IS NOT NULL") or 0,
        "seniority": scalar("ROUND(AVG(uj.seniority_score)::numeric,1)",
                            "AND uj.seniority_score IS NOT NULL") or 0,
        "domain": scalar("ROUND(AVG(uj.domain_score)::numeric,1)",
                         "AND uj.domain_score IS NOT NULL") or 0,
    }

    return {
        "funnel": funnel, "total": total, "avg_score": avg_score,
        "feed_size": feed_size, "distribution": dist, "sources": sources,
        "deadlines": deadlines, "rates": rates,
        "activity": activity, "work_mix": work_mix, "job_types": job_types,
        "languages": languages, "salary": salary, "companies": companies,
        "score_parts": score_parts,
    }


@router.get("/api/jobs")
def list_jobs(tab: str = "feed", sort: str = "score", source: str = "all",
              page: int = 1,
              user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    threshold = _user_threshold(conn, user_id)
    scoring_on = _get_setting(conn, "scoring_enabled", "1") == "1"

    # An unknown tab returns nothing — a visibly empty list is a clearer signal
    # than showing the feed under the wrong heading.
    if tab == "feed":
        if scoring_on:
            where = (f"uj.status='surfaced' AND uj.score IS NOT NULL "
                     f"AND uj.score >= {threshold}")
        else:
            # No scores to rank by — show all surfaced jobs rather than an empty page.
            where = "uj.status = 'surfaced'"
    elif tab in TAB_WHERE:
        where = TAB_WHERE[tab]
    else:
        where = "1 = 0"          # no such tab -> no rows

    params: list = [user_id]
    if source and source != "all":
        where += " AND j.source = ?"
        params.append(source)

    order = {"score": "uj.score DESC", "newest": "j.posted_date DESC",
             "company": "j.company ASC"}.get(sort, "uj.score DESC")   # whitelist
    if sort == "score" and (tab == "unscored" or (tab == "feed" and not scoring_on)):
        order = "j.id DESC"          # nothing to rank by; show the newest first

    # Paginated: 25 rows per page (?page=1,2,…). ORDER BY puts the best/newest first,
    # so page 1 is the most relevant slice; the client fetches more on demand. page and
    # the derived offset are ints, so they interpolate safely.
    page = max(1, int(page))
    offset = (page - 1) * _FEED_PAGE_SIZE
    rows = conn.execute(
        f"SELECT {FEED_COLS} FROM jobs j "
        f"JOIN user_jobs uj ON uj.job_id = j.id "
        f"WHERE uj.user_id = ? AND {where} "
        f"ORDER BY {order} LIMIT {_FEED_PAGE_SIZE} OFFSET {offset}",
        params).fetchall()
    return [dict(r) for r in rows]


class StatusUpdate(BaseModel):
    status: str


@router.post("/api/jobs/sweep-expired")
def sweep_expired(user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    """Dismiss every live job in THIS user's feed whose deadline has passed.

    Only 'surfaced' rows are touched: something already saved or applied to is the
    user's business, not a deadline's.
    """
    from src import expiry

    rows = conn.execute(
        "SELECT j.id, j.deadline, j.description FROM jobs j "
        "JOIN user_jobs uj ON uj.job_id = j.id "
        "WHERE uj.user_id = ? AND uj.status = 'surfaced'", (user_id,)
    ).fetchall()

    expired = [r[0] for r in rows
               if expiry.has_expired({"deadline": r[1], "description": r[2]})]

    if expired:
        with conn:
            conn.executemany(
                "UPDATE user_jobs SET status='dismissed' "
                "WHERE user_id=? AND job_id=?",
                [(user_id, i) for i in expired])

    return {"checked": len(rows), "dismissed": len(expired)}


@router.post("/api/jobs/{job_id}/status")
def set_status(job_id: int, body: StatusUpdate,
               user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    if body.status not in ALLOWED_STATUS:
        raise HTTPException(400, f"invalid status: {body.status}")
    if body.status == "applied":
        cur = conn.execute(
            "UPDATE user_jobs SET status=?, applied_on=COALESCE(applied_on, now()) "
            "WHERE user_id=? AND job_id=?",
            (body.status, user_id, job_id),
        )
    else:
        cur = conn.execute(
            "UPDATE user_jobs SET status=? WHERE user_id=? AND job_id=?",
            (body.status, user_id, job_id))
    conn.commit()
    if not cur.rowcount:
        raise HTTPException(404, "job not found")
    return {"id": job_id, "status": body.status}


class NotesUpdate(BaseModel):
    notes: str


@router.post("/api/jobs/{job_id}/notes")
def set_notes(job_id: int, body: NotesUpdate,
              user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    conn.execute("UPDATE user_jobs SET notes=? WHERE user_id=? AND job_id=?",
                 (body.notes, user_id, job_id))
    conn.commit()
    return {"ok": True}


@router.post("/api/jobs/{job_id}/viewed")
def mark_viewed(job_id: int, user_id: str = Depends(current_user_id),
                conn=Depends(_db_dep)):
    """Stamp the moment a job's detail was opened, so its card can show 'last seen'."""
    with conn:
        conn.execute(
            "UPDATE user_jobs SET last_viewed_at = now() "
            "WHERE user_id=? AND job_id=?", (user_id, job_id))
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
             user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    """Correct a job's fields by hand — for when a fetch grabbed the wrong text or
    only half a description. Only the fields you actually send are changed.

    Note: jobs is the SHARED pool, so an edit here changes the posting for everyone.
    (This may become admin-only.) Re-scoring is per-user and being reworked, so it's
    not run here; needs_reprocess flags whether the edit touched something a future
    re-score would care about.
    """
    exists = conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not exists:
        raise HTTPException(404, "job not found")

    changes = body.model_dump(exclude_unset=True)
    changes = {k: v for k, v in changes.items() if k in EDITABLE_FIELDS}
    if not changes:
        raise HTTPException(400, "no editable fields provided")

    # remote is a boolean column in Postgres; the API still takes 0/1.
    if "remote" in changes:
        changes["remote"] = bool(changes["remote"])

    assignments = ", ".join(f"{col}=?" for col in changes)   # col names from whitelist
    values = list(changes.values())
    values.append(job_id)
    conn.execute(f"UPDATE jobs SET {assignments} WHERE id=?", values)
    conn.commit()

    # Flag (don't run) a re-score: title/company/description changes affect the match.
    SCORING_FIELDS = {"title", "company", "description"}
    needs_reprocess = bool(set(changes) & SCORING_FIELDS)

    row = conn.execute(
        "SELECT j.id, j.title, j.company, j.location, j.description, j.apply_url, "
        "j.source_url, j.job_type, j.posted_date, j.deadline, j.remote, "
        "j.salary_min, j.salary_max, uj.score "
        "FROM jobs j "
        "LEFT JOIN user_jobs uj ON uj.job_id = j.id AND uj.user_id = ? "
        "WHERE j.id=?", (user_id, job_id)
    ).fetchone()
    return {"updated": list(changes), "rescored": None,
            "needs_reprocess": needs_reprocess, "job": dict(row)}


@router.post("/api/jobs/{job_id}/recheck")
def recheck_job(job_id: int, user_id: str = Depends(current_user_id),
                conn=Depends(_db_dep)):
    """Put an edited job back through the filters, for THIS user.

    A job that arrived with half a description was judged on half a description.
    Once you paste the real posting, the things that were unknowable become knowable
    — that it is in Austin, that it names a keyword you exclude — and a job that
    looked fine turns out not to be one you want. This re-runs the prefilter against
    your profile and, if it now fails, dismisses it in your feed and says which rule
    did it. Scoring is deliberately not done here — it's the slow half, and pointless
    on a job the filters just removed.
    """
    row = conn.execute(
        "SELECT id, title, company, location, description, job_type, remote, "
        "salary_min, salary_max, posted_date, source_url, apply_url "
        "FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "job not found")
    job = dict(row)

    from src.deps import _user_profile
    from src.scoring.prefilter import why_not

    profile = _user_profile(conn, user_id)
    if not profile:
        return {"verdict": "unchecked",
                "reason": "no profile set up, so there were no filters to apply"}

    reason = why_not(job, profile)
    if reason is None:
        return {"verdict": "ok", "reason": None}

    conn.execute("UPDATE user_jobs SET status='dismissed' "
                 "WHERE user_id=? AND job_id=?", (user_id, job_id))
    conn.commit()
    return {"verdict": "dismissed", "reason": reason}


def _rescore_one(conn, user_id, job_id: int, calibration: str | None = None):
    """Score a single job for THIS user now, and persist to their user_jobs row.
    Returns the new score, or None if scoring couldn't run (no model, no profile).

    `calibration` may be passed by a batch caller; building it reads the database,
    so doing it once for a whole rescore rather than once per job saves a query.
    """
    try:
        from src.deps import _user_profile
        from src.scoring.rerank import score_job, build_calibration

        profile = _user_profile(conn, user_id)
        if not profile:
            return None

        job = conn.execute(
            "SELECT title, company, location, description, job_type FROM jobs WHERE id=?",
            (job_id,)
        ).fetchone()
        if not job:
            return None

        if calibration is None:
            calibration = build_calibration(conn, user_id)
        result = score_job(dict(job), profile, calibration)
        if result is None:
            return None

        # `with conn:` so the row commits and the write lock releases the moment this
        # job is done — not held across the next job's multi-second model call.
        with conn:
            conn.execute(
                "UPDATE user_jobs SET score=?, skills_score=?, seniority_score=?, "
                "domain_score=?, rationale=? WHERE user_id=? AND job_id=?",
                (result.overall, result.skills_score, result.seniority_score,
                 result.domain_score, result.rationale, user_id, job_id),
            )
        return round(result.overall)
    except Exception:
        import traceback
        traceback.print_exc()          # full trace to the console; the edit still stands
        return None


# ── The "get new jobs" flow: score the shared pool for one user ───────────────
#
# The pool is filled by admin fetches (src/run.py) with no scoring. This is where a
# user turns that pool into THEIR feed: it takes the recent pool jobs they haven't
# seen, runs the cheap profile prefilter, scores the survivors against their profile
# (capped so one click can't spend an hour of model time), and writes a user_jobs row
# for each. Jobs that fail the prefilter get a dismissed row so they aren't re-checked
# every time; jobs past the score cap are left unseen for the next "get more".

_GET_NEW_CAP = 50               # max model scores per call
_FEED_PAGE_SIZE = 25            # feed rows per page (client paginates with ?page=)


@router.post("/api/jobs/get-new")
def get_new_jobs(user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    from src.deps import _user_profile
    from src.scoring.prefilter import why_not
    from src.scoring.rerank import build_calibration, score_job

    profile = _user_profile(conn, user_id)
    if not profile:
        return {"needs_profile": True, "scored": 0, "filtered": 0,
                "remaining": 0, "surfaced": []}

    scoring_on = _get_setting(conn, "scoring_enabled", "1") == "1"
    window = int(_get_setting(conn, "new_job_window_days", 5))

    # Recent pool jobs with no row yet for this user, newest first.
    rows = conn.execute(
        "SELECT j.id, j.title, j.company, j.location, j.description, j.job_type, "
        "j.remote, j.salary_min, j.salary_max, j.posted_date "
        "FROM jobs j LEFT JOIN user_jobs uj "
        "ON uj.job_id = j.id AND uj.user_id = ? "
        "WHERE uj.job_id IS NULL "
        "AND j.fetched_at >= now() - (? || ' days')::interval "
        "ORDER BY j.fetched_at DESC",
        (user_id, window)
    ).fetchall()

    # Fallback: nothing fetched inside the recent window, but the shared pool may
    # still hold older postings this user has never been offered — a new user, or a
    # quiet fetch week. Show the whole unseen pool (capped) so "get new" isn't empty
    # when there genuinely are jobs to surface.
    if not rows:
        rows = conn.execute(
            "SELECT j.id, j.title, j.company, j.location, j.description, j.job_type, "
            "j.remote, j.salary_min, j.salary_max, j.posted_date "
            "FROM jobs j LEFT JOIN user_jobs uj "
            "ON uj.job_id = j.id AND uj.user_id = ? "
            "WHERE uj.job_id IS NULL "
            "ORDER BY j.fetched_at DESC LIMIT 500",
            (user_id,)
        ).fetchall()

    calibration = build_calibration(conn, user_id) if scoring_on else ""
    scored = filtered = remaining = 0
    surfaced: list[dict] = []

    for r in rows:
        job = dict(r)

        # Cheap gate first. A failure is recorded as a dismissed row so this job
        # isn't re-evaluated on every future call.
        reason = why_not(job, profile)
        if reason is not None:
            with conn:
                conn.execute(
                    "INSERT INTO user_jobs (user_id, job_id, status, rationale, served_at) "
                    "VALUES (?,?, 'dismissed', ?, now()) "
                    "ON CONFLICT (user_id, job_id) DO NOTHING",
                    (user_id, job["id"], f"filtered: {reason}"[:500]))
            filtered += 1
            continue

        # Passed the gate. Beyond the cap, leave it unseen for the next call.
        if scored >= _GET_NEW_CAP:
            remaining += 1
            continue

        # score_job() is a per-job LLM call taking seconds. It runs with NO open
        # transaction (the previous job already committed via `with conn:`), so the
        # DB session never sits idle-in-transaction across the model call — the same
        # failure ("connection is lost") that a single end-of-loop commit caused.
        result = score_job(job, profile, calibration) if scoring_on else None
        if result is not None:
            with conn:
                conn.execute(
                    "INSERT INTO user_jobs (user_id, job_id, status, score, skills_score, "
                    "seniority_score, domain_score, rationale, served_at) "
                    "VALUES (?,?, 'surfaced', ?,?,?,?,?, now()) "
                    "ON CONFLICT (user_id, job_id) DO NOTHING",
                    (user_id, job["id"], result.overall, result.skills_score,
                     result.seniority_score, result.domain_score, result.rationale))
            surfaced.append({"id": job["id"], "title": job["title"],
                             "company": job["company"], "score": result.overall})
            scored += 1
        else:
            # Scoring off, or no usable description: surface unscored (NULL score),
            # the same honest "not judged yet" the import path uses.
            with conn:
                conn.execute(
                    "INSERT INTO user_jobs (user_id, job_id, status, served_at) "
                    "VALUES (?,?, 'surfaced', now()) "
                    "ON CONFLICT (user_id, job_id) DO NOTHING",
                    (user_id, job["id"]))
            surfaced.append({"id": job["id"], "title": job["title"],
                             "company": job["company"], "score": None})
            scored += 1

    return {"scored": scored, "filtered": filtered, "remaining": remaining,
            "surfaced": surfaced}


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
def match_job(url: str, user_id: str = Depends(current_user_id),
              conn=Depends(_db_dep)):
    """Find the job this browser page belongs to.

    Matches against the whole pool by URL; the status returned is THIS user's
    status for the job (null if it isn't in their feed). Confidence is conservative:
    anything below an exact host+path match is a suggestion, never an auto-binding.
    """
    target = _normalize_url(url)
    if not target:
        return {"match": None, "candidates": []}

    rows = conn.execute(
        "SELECT j.id, j.title, j.company, j.apply_url, j.source_url, uj.status "
        "FROM jobs j "
        "LEFT JOIN user_jobs uj ON uj.job_id = j.id AND uj.user_id = ? "
        "WHERE j.apply_url IS NOT NULL OR j.source_url IS NOT NULL", (user_id,)
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
def search_jobs(q: str = "", limit: int = 10,
                user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    """Free-text search over title/company, for the extension's manual picker.

    Only jobs still in play in THIS user's feed — surfaced or saved — are offered.
    """
    like = f"%{q.strip()}%"
    rows = conn.execute(
        "SELECT j.id, j.title, j.company, uj.status FROM jobs j "
        "JOIN user_jobs uj ON uj.job_id = j.id "
        "WHERE uj.user_id = ? AND (j.title LIKE ? OR j.company LIKE ?) "
        "AND uj.status IN ('surfaced', 'saved') "
        "ORDER BY CASE uj.status WHEN 'saved' THEN 0 ELSE 1 END, "
        "uj.score DESC LIMIT ?",
        (user_id, like, like, limit),
    ).fetchall()
    return [dict(r) for r in rows]
