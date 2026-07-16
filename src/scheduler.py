"""In-app scheduler — runs the pipeline every N hours while the server is up.

Interval and on/off live in the `settings` table, so they survive restarts and
are editable from the frontend. `last_run_ts` is also persisted, which is what
makes catch-up work: if the machine was off past the due time, the next loop
tick after startup fires the run.
"""
import threading
import time
from datetime import datetime, timedelta

from src import store, notify
from src.paths import DEFAULT_RUN_INTERVAL_HOURS as DEFAULT_HOURS, SCHEDULER_POLL_SECONDS as POLL_SECONDS
from src.logs import log
from src.env import load_env

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


def _run_once(only=None):
    from src.run import run as run_pipeline

    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True

    try:
        run_pipeline(only=only)
        _state["last_summary"] = ("completed (selective)" if only else "completed")
    except Exception as e:
        # A pipeline crash used to live in a print() and this dict, and the next
        # restart erased both. Now it is a row you can read, and a message on your
        # phone the moment it happens.
        _state["last_summary"] = f"error: {e}"
        log.exception("[scheduler] pipeline failed")

        sent = notify.send(
            f"\u26a0\ufe0f <b>Pipeline failed</b>\n"
            f"<code>{type(e).__name__}: {str(e)[:200]}</code>"
        )
        try:
            conn = store.connect()
            store.record_error(conn, "pipeline", e, notified=sent)
            conn.close()
        except Exception as inner:
            log.error("[scheduler] could not record the error: %s", inner)
    finally:
        now = datetime.now()
        _state["running"] = False
        _state["last_run"] = now.strftime("%Y-%m-%d %H:%M")
        _stamp_last_run(now)
    return True


def trigger_async(only=None) -> bool:
    """Fire a run in the background. False if one is already going.

    `only` is an optional list of source names to fetch just those (a selective run);
    None means a normal full run over every active source.
    """
    if _state["running"]:
        return False
    threading.Thread(target=_run_once, kwargs={"only": only}, daemon=True).start()
    return True


def _board_alerts():
    """Tell you the moment a working board goes dark.

    Runs right after a pipeline pass, when the streaks are fresh. Each broken
    board is reported once — the alert flag is cleared automatically if it starts
    working again, so a board that breaks twice tells you twice.
    """
    from src import health, notify, store
    from src.paths import HEALTH_ALERTS

    if not HEALTH_ALERTS or not notify.enabled():
        return

    conn = store.connect()
    try:
        fresh = health.new_breakages(conn)
        if not fresh:
            return
        message = notify.board_alert(fresh)
        store.mark_health_alerted(conn, [b["name"] for b in fresh])
    finally:
        conn.close()

    notify.send(message)


def _weekly_digest():
    """Once a week. Guarded by the ISO week number, so a restart doesn't resend it
    and a machine that was off all week sends one digest when it comes back."""
    from src import health, notify, store
    from src.paths import WEEKLY_DIGEST, WEEKLY_DIGEST_WEEKDAY, WEEKLY_DIGEST_HOUR

    if not WEEKLY_DIGEST or not notify.enabled():
        return

    now = datetime.now()
    if now.weekday() != WEEKLY_DIGEST_WEEKDAY or now.hour < WEEKLY_DIGEST_HOUR:
        return

    year, week, _ = now.isocalendar()
    tag = f"{year}-W{week:02d}"

    conn = store.connect()
    try:
        if store.get_setting(conn, "digest_sent_week", None) == tag:
            return
        stats = health.week_stats(conn)
        store.set_setting(conn, "digest_sent_week", tag)
    finally:
        conn.close()

    notify.send(notify.weekly_digest(stats))


def _followup_check():
    """Once a day, tell you what needs a nudge.

    Guarded by a stored date rather than a timer, so restarting the server does
    not re-send today's reminder — and a machine that was off for three days gets
    one message when it comes back, not three.
    """
    from src import followups, notify
    from src.paths import FOLLOWUP_NOTIFY

    if not FOLLOWUP_NOTIFY:
        return

    today = datetime.now().date().isoformat()
    conn = store.connect()
    try:
        if store.get_setting(conn, "followup_notified_on", None) == today:
            return
        message = followups.notification(conn)
        store.set_setting(conn, "followup_notified_on", today)
    finally:
        conn.close()

    if message and notify.enabled():
        notify.send(message)


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
                log.info("[scheduler] interval elapsed — starting run")
                _run_once()
                _board_alerts()          # streaks are freshest right after a run

            _followup_check()
            _weekly_digest()
        except Exception as e:
            # The scheduler loop is the one place a crash means the app quietly stops
            # doing its job — no run, no follow-up check, and nobody watching. Print
            # scrolls past; record it so it is visible in the UI and on Telegram, the
            # same way a pipeline crash is.
            log.exception("[scheduler] loop error")
            try:
                sent = notify.send(
                    f"\u26a0\ufe0f <b>Scheduler loop error</b>\n"
                    f"<code>{type(e).__name__}: {str(e)[:200]}</code>")
                conn = store.connect()
                store.record_error(conn, "scheduler:loop", e, notified=sent)
                conn.close()
            except Exception as inner:
                log.error("[scheduler] could not record the loop error: %s", inner)

        time.sleep(POLL_SECONDS)


def start():
    load_env()
    threading.Thread(target=_loop, daemon=True).start()
    log.info("[scheduler] started")
