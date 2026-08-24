"""Maintenance operations: rescore, cleanup, export, reset, etc."""
import os
import shutil
from datetime import datetime, timedelta

from src import store
from src.config import load_companies, load_profile
from src.paths import ROOT, BACKUP_DIR


_STAGE5 = ("Per-user scoring isn't wired up yet — this returns once the "
           "get-new-jobs / rescore flow lands (Stage 5).")


def rescore_all():
    """Re-run AI scoring on all stored jobs. PENDING: scoring is per-user now
    (user_jobs), so a system-wide rescore no longer makes sense — this will be
    rebuilt as a per-user rescore in the get-new-jobs flow."""
    raise NotImplementedError(_STAGE5)


def cleanup_below_threshold():
    """Archive surfaced jobs below threshold. PENDING: status/score live in
    user_jobs now, so cleanup is per-user."""
    raise NotImplementedError(_STAGE5)


def clear_old_jobs(days: int):
    """Permanently delete jobs older than N days (by fetched_at)."""
    conn = store.connect()
    # DB-side interval so the cutoff is computed in the same (UTC) clock as the
    # fetched_at timestamptz — a python datetime.now() would be the server's local time.
    cur = conn.execute("DELETE FROM jobs WHERE fetched_at < now() - (? || ' days')::interval", (days,))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return {"deleted": n}


def prune_stale_jobs(days: int):
    """Auto-retention: delete pool jobs older than N days that NO user has engaged
    with. Unlike clear_old_jobs (a manual full purge), this preserves anything a user
    saved or applied to, or generated materials / saved answers for — so cleanup never
    silently erases a user's history, however old the posting is.
    """
    conn = store.connect()
    cur = conn.execute(
        "DELETE FROM jobs j WHERE j.fetched_at < now() - (? || ' days')::interval "
        "AND NOT EXISTS (SELECT 1 FROM user_jobs uj "
        "                WHERE uj.job_id = j.id AND uj.status IN ('saved','applied')) "
        "AND NOT EXISTS (SELECT 1 FROM materials m WHERE m.job_id = j.id) "
        "AND NOT EXISTS (SELECT 1 FROM application_answers a WHERE a.job_id = j.id)",
        (days,)
    )
    conn.commit()
    n = cur.rowcount
    conn.close()
    return {"deleted": n}


def export_csv() -> str:
    """Return all jobs as a CSV string. PENDING: score/status are per-user now,
    so a meaningful export is per-user (whose feed?) — rebuilt in Stage 5."""
    raise NotImplementedError(_STAGE5)


def reload_config():
    """Force re-read of profile.yaml + companies.yaml (validates them)."""
    p = load_profile()
    c = load_companies()
    return {"profile_ok": bool(p), "companies": len(c)}


def schedule_restart():
    """Restart the whole process a moment after replying.

    We reply first so the browser gets a clean 200, then re-exec on a short timer.
    os.execv replaces the current process image with a fresh one — same command line —
    so it works whether or not there's a supervisor, as long as something keeps the
    terminal open (the loop script does). The delay lets this response flush before the
    process is replaced.
    """
    import os
    import sys
    import threading

    def _restart():
        # Replace this process with a fresh interpreter running the same argv. On
        # Windows, execv works; the tunnel reconnects once the port is back.
        python = sys.executable
        os.execv(python, [python] + sys.argv)

    threading.Timer(0.5, _restart).start()
    return {"restarting": True}


def clean_cache():
    """Delete Python cache and any *.log files. Jobs and config untouched."""
    removed_dirs = 0
    removed_files = 0
    for root, dirs, files in os.walk(ROOT):
        for d in list(dirs):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                removed_dirs += 1
        for f in files:
            if f.endswith(".log"):
                try:
                    os.remove(os.path.join(root, f))
                    removed_files += 1
                except OSError:
                    pass
    return {"cache_dirs": removed_dirs, "log_files": removed_files}


def clear_run_history():
    """Wipe the run-history table (the Admin tab list). Jobs are untouched."""
    conn = store.connect()
    cur = conn.execute("DELETE FROM runs")
    conn.commit()
    n = cur.rowcount
    conn.close()
    return {"cleared_runs": n}


def reset_all_jobs():
    """DESTRUCTIVE: wipe jobs + seen tables. Config/settings preserved."""
    conn = store.connect()
    conn.execute("DELETE FROM jobs")
    conn.execute("DELETE FROM seen")
    conn.execute("DELETE FROM source_health")
    conn.commit()
    conn.close()
    return {"reset": True}


def nuclear_reset():
    """DESTRUCTIVE: wipe all application data in one shot.

    Deletes: jobs, the seen/dedupe log, source health, run history and AI quota
    tracking; empties the backups directory and the Python cache.

    Preserves: your config files (config/profile.yaml, config/companies.yaml),
    your .env, and your settings (threshold, schedule, provider order) — so the
    app starts fresh but still behaves the way you configured it.
    """
    conn = store.connect()
    for table in ("jobs", "seen", "source_health", "runs", "llm_usage"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()

    # Empty the backups directory (config .bak copies), keeping the folder.
    backups = 0
    if BACKUP_DIR.exists():
        for f in BACKUP_DIR.iterdir():
            if f.is_file() and f.name != ".gitkeep":
                f.unlink(missing_ok=True)
                backups += 1

    cache = clean_cache()
    return {"wiped": True, "backups_removed": backups, **cache}
