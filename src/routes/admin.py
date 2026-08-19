"""Maintenance, error log, run history, and manual pipeline triggers.

The housekeeping surface: rescore everything against the current profile, clear jobs
below the threshold or older than N days, export to CSV, reload config, empty caches,
and the two destructive resets. Plus the read-only views the admin panel shows — the
error log and the fetch-run history — and the button that kicks off a fetch by hand.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src import configio, maintenance, scheduler, store
from src.auth import current_user_id
from src.deps import _db_dep, require_admin

router = APIRouter()


@router.get("/api/setup-status")
def setup_status(_: str = Depends(current_user_id), conn=Depends(_db_dep)):
    """Tell the UI how far along setup is, so a new user gets a checklist instead of a
    blank feed. Each step is a plain boolean plus a short label the frontend renders."""
    profile = configio.read_yaml("profile.yaml") or {}
    companies = (configio.read_yaml("companies.yaml") or {}).get("companies", [])
    active_sources = [c for c in companies if c.get("active")]

    # A profile is "set up" once it has any of the fields that actually drive scoring —
    # not just an empty file with headers.
    prof_search = profile.get("search", {}) or {}
    prof_constraints = profile.get("constraints", {}) or {}
    has_profile = bool(prof_search or prof_constraints)

    def _count(sql):
        try:
            return conn.execute(sql).fetchone()[0]
        except Exception:                              # table not created yet
            return 0

    runs = _count("SELECT COUNT(*) FROM runs")
    total_jobs = _count("SELECT COUNT(*) FROM jobs")

    steps = [
        {"key": "profile", "done": has_profile,
         "label": "Set up your profile",
         "hint": "Tell JobPilot what roles, locations and level you want."},
        {"key": "sources", "done": len(active_sources) > 0,
         "label": "Add job sources",
         "hint": "Pick the company boards to fetch jobs from."},
        {"key": "run", "done": runs > 0,
         "label": "Run your first fetch",
         "hint": "Pull jobs from your sources and score them against your profile."},
        {"key": "review", "done": total_jobs > 0,
         "label": "Review your matches",
         "hint": "See scored jobs in your feed and start applying."},
    ]
    done = sum(1 for s in steps if s["done"])
    return {
        "steps": steps,
        "completed": done,
        "total": len(steps),
        "all_done": done == len(steps),
        "active_sources": len(active_sources),
    }


# ── Maintenance ──

@router.post("/api/maint/rescore")
def maint_rescore(_: str = Depends(require_admin)):
    raise HTTPException(503, "Pending Stage 5: per-user scoring/enrichment rework")


class ScoreRequest(BaseModel):
    job_ids: list[int]


@router.get("/api/notifications")
def list_notifications(user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    """Recent notifications for the bell in the UI, newest first, with an unseen count."""
    rows = conn.execute(
        "SELECT id, text, created_at, seen FROM notifications "
        "WHERE user_id = ? ORDER BY id DESC LIMIT 50",
        (user_id,)
    ).fetchall()
    items = [{"id": r[0], "text": r[1], "created_at": r[2], "seen": bool(r[3])}
             for r in rows]
    unseen = sum(1 for it in items if not it["seen"])
    return {"notifications": items, "unseen": unseen}


@router.post("/api/notifications/seen")
def mark_notifications_seen(user_id: str = Depends(current_user_id),
                            conn=Depends(_db_dep)):
    """Mark everything as seen — called when the user opens the notifications panel."""
    with conn:
        conn.execute("UPDATE notifications SET seen = true "
                     "WHERE user_id = ? AND seen = false", (user_id,))
    return {"ok": True}


@router.get("/api/jobs/enrich-diagnosis")
def enrich_diagnosis(_: str = Depends(require_admin)):
    """Where do the feed's Adzuna links point? PENDING: coupled to per-user status
    and the enrichment path; rebuilt for the pool in Stage 5."""
    raise HTTPException(503, "Pending Stage 5: per-user scoring/enrichment rework")



@router.post("/api/jobs/enrich-existing")
def enrich_existing(_: str = Depends(require_admin)):
    """Fetch full descriptions for short feed jobs and rescore. PENDING: selects by
    per-user status and calls per-user rescore; rebuilt for the pool in Stage 5."""
    raise HTTPException(503, "Pending Stage 5: per-user scoring/enrichment rework")



@router.post("/api/jobs/extract-existing")
def extract_existing(_: str = Depends(require_admin), conn=Depends(_db_dep)):
    """Backfill the structured fields for jobs that predate extraction.

    Extraction runs during a fetch, so every job saved before this feature existed
    carries a NULL for work_mode, requirements, benefits and the rest. This walks
    the jobs that have a real description but were never extracted, runs one LLM
    pass over each, and writes the fields in place — the score and everything else
    is left untouched.

    Capped per request so one click can't run for an hour on a huge database; the
    counts come back so the UI can tell you to run it again for the rest.
    """
    from src import extract, notify

    # A description too short to extract from would just come back empty — skip it,
    # the same bar the live pipeline uses.
    rows = conn.execute(
        "SELECT id, title, company, location, description "
        "FROM jobs "
        "WHERE extracted_at IS NULL "
        "AND description IS NOT NULL "
        "AND length(trim(description)) >= ?",
        (extract.MIN_DESCRIPTION_CHARS,),
    ).fetchall()

    remaining = len(rows)
    extracted = 0
    skipped = 0        # had a description but the model found nothing to read
    failed = 0         # the call errored — logged, job left NULL to retry later
    for row in rows[:100]:                 # cap one request; run again for the rest
        job = {"title": row[1], "company": row[2], "location": row[3],
               "description": row[4]}
        try:
            ex = extract.extract(job)
        except Exception as e:
            failed += 1
            store.record_error(conn, "extract:backfill", e)
            continue
        if ex is None:
            skipped += 1
            continue
        store.update_extraction(conn, row[0], ex.model_dump())
        extracted += 1

    notify.send(
        "<b>Description extraction — backfill</b>\n"
        f"Extracted: {extracted}\n"
        f"Nothing to read: {skipped}   Failed: {failed}\n"
        f"({remaining} were pending; run again for the rest)"
    )

    return {
        "checked": min(remaining, 100),
        "extracted": extracted,
        "skipped": skipped,
        "failed": failed,
        "remaining": max(0, remaining - 100),
    }


@router.post("/api/jobs/score")
def score_jobs(_: str = Depends(require_admin)):
    """Score specific jobs on demand. PENDING: scoring is per-user now (user_jobs);
    rebuilt in the get-new-jobs flow (Stage 5)."""
    raise HTTPException(503, "Pending Stage 5: per-user scoring/enrichment rework")


@router.post("/api/maint/cleanup")
def maint_cleanup(_: str = Depends(require_admin)):
    raise HTTPException(503, "Pending Stage 5: per-user scoring/enrichment rework")


class DaysBody(BaseModel):
    days: int = 30


@router.post("/api/maint/clear-old")
def maint_clear_old(body: DaysBody, _: str = Depends(require_admin)):
    return maintenance.clear_old_jobs(body.days)


@router.get("/api/maint/export")
def maint_export(_: str = Depends(require_admin)):
    raise HTTPException(503, "Pending Stage 5: per-user scoring/enrichment rework")


@router.post("/api/maint/reload")
def maint_reload(_: str = Depends(require_admin)):
    return maintenance.reload_config()


@router.post("/api/maint/restart")
def maint_restart(_: str = Depends(require_admin)):
    """Restart the server process.

    Config reload re-reads the YAML files but keeps the running code and any stuck
    in-memory state. A full restart re-execs the process, which is what you want after
    pulling new code or when the server is wedged. It replies first, then restarts a
    moment later so this request completes cleanly (the browser then reconnects on its
    own). If the server is run under a supervisor that respawns it, or with --reload,
    this comes back on its own; a bare `uvicorn` call will not, so run it from the
    provided loop script.
    """
    return maintenance.schedule_restart()


@router.post("/api/maint/clean-cache")
def maint_clean_cache(_: str = Depends(require_admin)):
    return maintenance.clean_cache()


@router.post("/api/maint/reset")
def maint_reset(_: str = Depends(require_admin)):
    return maintenance.reset_all_jobs()


@router.post("/api/maint/clear-runs")
def maint_clear_runs(_: str = Depends(require_admin)):
    return maintenance.clear_run_history()


@router.post("/api/maint/nuclear")
def maint_nuclear(_: str = Depends(require_admin)):
    return maintenance.nuclear_reset()


@router.get("/api/maint/preview")
def maint_preview(_: str = Depends(require_admin), conn=Depends(_db_dep)):
    """Live counts of what each maintenance action would touch.

    Pool-wide metrics (total jobs, unextracted backfill target, cache size) are exact.
    The per-user feed metrics (short snippets, expired, below-threshold) depend on
    status/score, which moved to user_jobs — they read 0 until the per-user cleanup
    lands in Stage 5, so the tab shows a clear target for the actions that do work now.
    """
    from pathlib import Path

    def q(sql, *args):
        return conn.execute(sql, args).fetchone()[0]

    total = q("SELECT COUNT(*) FROM jobs")

    # Per-user feed metrics — pending Stage 5 (status/score live in user_jobs now).
    snippets = 0
    expired = 0
    low = 0

    # Jobs with a real description that were never run through extraction — the
    # backfill target for extract-existing. Pool-wide, exact.
    from src.extract import MIN_DESCRIPTION_CHARS as _MIN_EXTRACT
    unextracted = q(
        "SELECT COUNT(*) FROM jobs WHERE extracted_at IS NULL "
        "AND description IS NOT NULL AND length(trim(description)) >= ?",
        _MIN_EXTRACT,
    )

    # Rough cache size on disk, in MB.
    cache_bytes = 0
    for name in ("__pycache__", "logs", "data/cache"):
        p = Path(name)
        if p.exists():
            cache_bytes += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    cache_mb = round(cache_bytes / (1024 * 1024), 1)

    return {
        "total": total,
        "snippets": snippets,
        "expired": expired,
        "low": low,
        "unextracted": unextracted,
        "cache_mb": cache_mb,
    }


# ── Error log ──

@router.get("/api/errors")
def errors_list(limit: int = 100, _: str = Depends(require_admin), conn=Depends(_db_dep)):
    """Everything that has gone wrong, newest first."""
    rows = store.recent_errors(conn, limit)
    return rows


@router.post("/api/errors/clear")
def errors_clear(_: str = Depends(require_admin), conn=Depends(_db_dep)):
    n = store.clear_errors(conn)
    return {"cleared": n}


# ── Run history ──

@router.get("/api/runs")
def runs_list(limit: int = 50, _: str = Depends(require_admin), conn=Depends(_db_dep)):
    """The fetch history — what each run pulled in and kept."""
    rows = store.recent_runs(conn, limit)
    return rows


# ── Manual pipeline trigger ──

class RunRequest(BaseModel):
    # Optional list of source names to fetch just those (a selective run). Omit for a
    # normal full run over every active source.
    only: list[str] | None = None


@router.post("/api/run")
def trigger_run(body: RunRequest | None = None, _: str = Depends(require_admin)):
    only = body.only if body else None
    if not scheduler.trigger_async(only=only):
        raise HTTPException(409, "pipeline already running")
    return {"started": True, "selective": bool(only), "sources": only or []}


@router.get("/api/run/status")
def run_status(_: str = Depends(require_admin)):
    """Whether a run is going, and how far along it is.

    The UI polls this to show a run in progress. "Running" on its own is not much use
    when a pass can take twenty minutes; the counts let it say how far in, and the model
    line says what is doing the work.
    """
    from src.run import PROGRESS
    from src.scoring.rerank import get_model_state

    state = scheduler.get_state()
    state["progress"] = dict(PROGRESS)
    state["model"] = get_model_state()
    return state
