"""Maintenance, error log, run history, and manual pipeline triggers.

The housekeeping surface: rescore everything against the current profile, clear jobs
below the threshold or older than N days, export to CSV, reload config, empty caches,
and the two destructive resets. Plus the read-only views the admin panel shows — the
error log and the fetch-run history — and the button that kicks off a fetch by hand.
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from src import maintenance, scheduler, store
from src.deps import _db_dep, _get_setting

router = APIRouter()


# ── Maintenance ──

@router.post("/api/maint/rescore")
def maint_rescore():
    return maintenance.rescore_all()


class ScoreRequest(BaseModel):
    job_ids: list[int]


@router.post("/api/jobs/score")
def score_jobs(body: ScoreRequest, conn=Depends(_db_dep)):
    """Score specific jobs on demand — for unscored imports, or to re-run a few.

    Unlike 'rescore everything', this targets only the ids you pass, so you can score
    one job from its card, or a selection, without churning the whole database. Returns
    per-job results so the UI can update just those rows.
    """
    if _get_setting(conn, "scoring_enabled", "1") != "1":
        raise HTTPException(403, "Scoring is off — enable it in Settings first.")

    from src.routes.jobs import _rescore_one
    results = {}
    for jid in body.job_ids[:200]:          # cap a single request
        results[jid] = _rescore_one(conn, jid)
    scored = sum(1 for v in results.values() if v is not None)
    return {"requested": len(body.job_ids), "scored": scored, "results": results}


@router.post("/api/maint/cleanup")
def maint_cleanup():
    return maintenance.cleanup_below_threshold()


class DaysBody(BaseModel):
    days: int = 30


@router.post("/api/maint/clear-old")
def maint_clear_old(body: DaysBody):
    return maintenance.clear_old_jobs(body.days)


@router.get("/api/maint/export")
def maint_export():
    csv_data = maintenance.export_csv()
    return Response(content=csv_data, media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=jobpilot_jobs.csv"})


@router.post("/api/maint/reload")
def maint_reload():
    return maintenance.reload_config()


@router.post("/api/maint/restart")
def maint_restart():
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
def maint_clean_cache():
    return maintenance.clean_cache()


@router.post("/api/maint/reset")
def maint_reset():
    return maintenance.reset_all_jobs()


@router.post("/api/maint/clear-runs")
def maint_clear_runs():
    return maintenance.clear_run_history()


@router.post("/api/maint/nuclear")
def maint_nuclear():
    return maintenance.nuclear_reset()


# ── Error log ──

@router.get("/api/errors")
def errors_list(limit: int = 100, conn=Depends(_db_dep)):
    """Everything that has gone wrong, newest first."""
    rows = store.recent_errors(conn, limit)
    return rows


@router.post("/api/errors/clear")
def errors_clear(conn=Depends(_db_dep)):
    n = store.clear_errors(conn)
    return {"cleared": n}


# ── Run history ──

@router.get("/api/runs")
def runs_list(limit: int = 50, conn=Depends(_db_dep)):
    """The fetch history — what each run pulled in and kept."""
    rows = store.recent_runs(conn, limit)
    return rows


# ── Manual pipeline trigger ──

class RunRequest(BaseModel):
    # Optional list of source names to fetch just those (a selective run). Omit for a
    # normal full run over every active source.
    only: list[str] | None = None


@router.post("/api/run")
def trigger_run(body: RunRequest | None = None):
    only = body.only if body else None
    if not scheduler.trigger_async(only=only):
        raise HTTPException(409, "pipeline already running")
    return {"started": True, "selective": bool(only), "sources": only or []}


@router.get("/api/run/status")
def run_status():
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
