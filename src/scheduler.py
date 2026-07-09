"""In-app scheduler — runs the pipeline every N hours while the server is up.

Interval and on/off live in the `settings` table, so they survive restarts and
are editable from the frontend. `last_run_ts` is also persisted, which is what
makes catch-up work: if the machine was off past the due time, the next loop
tick after startup fires the run.
"""
import threading
import time
from datetime import datetime, timedelta

from src import store
from src.paths import DEFAULT_RUN_INTERVAL_HOURS as DEFAULT_HOURS, SCHEDULER_POLL_SECONDS as POLL_SECONDS

_state = {"running": False, "last_run": None, "last_summary": None, "next_run": None}
_lock = threading.Lock()


def get_state() -> dict:
    return dict(_state)


def _settings():
    conn = store.connect()
    enabled = store.get_setting(conn, "scheduler_enabled", "1") == "1"
    try:
        hours = float(store.get_setting(conn, "run_interval_hours", DEFAULT_HOURS) or DEFAULT_HOURS)
    except (TypeError, ValueError):
        hours = DEFAULT_HOURS
    last = store.get_setting(conn, "last_run_ts", None)
    conn.close()
    return enabled, max(0.5, hours), last


def _stamp_last_run(when: datetime):
    conn = store.connect()
    store.set_setting(conn, "last_run_ts", when.isoformat(timespec="seconds"))
    conn.close()


def _run_once():
    from src.run import run as run_pipeline

    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True

    try:
        run_pipeline()
        _state["last_summary"] = "completed"
    except Exception as e:
        _state["last_summary"] = f"error: {e}"
        print(f"[scheduler] pipeline failed: {e}")
    finally:
        now = datetime.now()
        _state["running"] = False
        _state["last_run"] = now.strftime("%Y-%m-%d %H:%M")
        _stamp_last_run(now)
    return True


def trigger_async() -> bool:
    """Fire a run in the background. False if one is already going."""
    if _state["running"]:
        return False
    threading.Thread(target=_run_once, daemon=True).start()
    return True


def _loop():
    # On first ever boot there is no last_run_ts. Stamp it now rather than
    # kicking off a long run the moment the server starts.
    _, _, last = _settings()
    if not last:
        _stamp_last_run(datetime.now())

    while True:
        try:
            enabled, hours, last = _settings()
            last_dt = datetime.fromisoformat(last) if last else None

            if last_dt:
                due_at = last_dt + timedelta(hours=hours)
                _state["last_run"] = last_dt.strftime("%Y-%m-%d %H:%M")
                _state["next_run"] = due_at.strftime("%Y-%m-%d %H:%M") if enabled else None
                due = datetime.now() >= due_at
            else:
                due = False

            if enabled and due and not _state["running"]:
                print("[scheduler] interval elapsed — starting run")
                _run_once()
        except Exception as e:
            print(f"[scheduler] loop error: {e}")

        time.sleep(POLL_SECONDS)


def start():
    threading.Thread(target=_loop, daemon=True).start()
    print("[scheduler] started")
