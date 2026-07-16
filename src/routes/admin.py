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
from src.deps import _db_dep

router = APIRouter()


# ── Maintenance ──

@router.post("/api/maint/rescore")
def maint_rescore():
    return maintenance.rescore_all()


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
    return scheduler.get_state()
