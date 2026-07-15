"""SQLite persistence: seen-log dedup + kept jobs."""
import sqlite3

from src.paths import DB_PATH as DB


def connect():
    return sqlite3.connect(DB)


def already_seen(conn, dedupe_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM seen WHERE dedupe_hash = ?", (dedupe_hash,)
    ).fetchone()
    return row is not None


def mark_seen(conn, dedupe_hash: str, decision: str, score: float | None = None):
    conn.execute(
        "INSERT OR IGNORE INTO seen (dedupe_hash, decision, score) VALUES (?, ?, ?)",
        (dedupe_hash, decision, score),
    )


def save_job(conn, job: dict):
    # job_type, deadline and the salary pair used to be missing from this list, so
    # adapters filled them and the columns stayed NULL — which quietly disabled
    # profile.yaml's `salary_floor` filter. Anything the schema stores must be
    # named here.
    conn.execute(
        """INSERT OR IGNORE INTO jobs
           (dedupe_hash, source, source_url, apply_url, title, company,
            location, remote, description, posted_date, score, skills_score,
            seniority_score, domain_score, rationale, flags,
            job_type, deadline, salary_min, salary_max)
           VALUES (:dedupe_hash, :source, :source_url, :apply_url, :title,
                   :company, :location, :remote, :description, :posted_date,
                   :score, :skills_score, :seniority_score, :domain_score,
                   :rationale, :flags,
                   :job_type, :deadline, :salary_min, :salary_max)""",
        job,
    )


def save_source_health(conn, name, ats, stat, when):
    """Record what a board did this run, and how long it has been doing it.

    The streaks are the point. A single empty fetch means nothing — a company
    genuinely might have no openings today. Three in a row from a board that used
    to return twenty is a broken selector, a changed API, or a company that quietly
    left the ATS. Without a streak you cannot tell those apart, and a board that
    returns 200-OK-and-nothing will sit in the Health tab looking green forever.
    """
    fetched = stat["fetched"]
    failed = stat["status"] == "error"

    prior = conn.execute(
        "SELECT zero_streak, error_streak, last_ok, alerted FROM source_health "
        "WHERE name = ?", (name,)
    ).fetchone()
    zero_streak, error_streak, last_ok, alerted = prior or (0, 0, None, 0)

    if failed:
        error_streak += 1
    elif fetched == 0:
        zero_streak += 1
        error_streak = 0
    else:
        # It worked. Everything resets, including the alert — so if it breaks
        # again later you get told again.
        zero_streak = 0
        error_streak = 0
        last_ok = when
        alerted = 0

    conn.execute(
        """INSERT INTO source_health
           (name, ats, fetched, kept, status, error, last_run,
            zero_streak, error_streak, last_ok, alerted)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET
             ats=excluded.ats, fetched=excluded.fetched, kept=excluded.kept,
             status=excluded.status, error=excluded.error,
             last_run=excluded.last_run, zero_streak=excluded.zero_streak,
             error_streak=excluded.error_streak, last_ok=excluded.last_ok,
             alerted=excluded.alerted""",
        (name, ats, fetched, stat["kept"], stat["status"], stat["error"], when,
         zero_streak, error_streak, last_ok, alerted),
    )
    conn.commit()


def mark_health_alerted(conn, names: list[str]):
    """Don't report the same broken board every single run."""
    if not names:
        return
    conn.executemany("UPDATE source_health SET alerted = 1 WHERE name = ?",
                     [(n,) for n in names])
    conn.commit()


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()


# ── Errors: something you can look back on ─────────────────────────────────────

def record_error(conn, where: str, exc: BaseException,
                 notified: bool = False) -> int:
    """Keep one exception. Returns its id.

    The pipeline used to fail into a print() and an in-memory string the next
    restart erased. This is the difference between "it broke last night" and "it
    broke last night at 2:14, here, with this traceback".
    """
    import traceback as _tb

    tb = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
    cur = conn.execute(
        "INSERT INTO errors (where_, kind, message, traceback, notified) "
        "VALUES (?, ?, ?, ?, ?)",
        (where, type(exc).__name__, str(exc), tb, 1 if notified else 0),
    )
    conn.commit()
    return cur.lastrowid


def recent_errors(conn, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT id, at, where_, kind, message, traceback, notified "
        "FROM errors ORDER BY at DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [
        {"id": r[0], "at": r[1], "where": r[2], "kind": r[3],
         "message": r[4], "traceback": r[5], "notified": bool(r[6])}
        for r in rows
    ]


def clear_errors(conn) -> int:
    n = conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
    conn.execute("DELETE FROM errors")
    conn.commit()
    return n


def recent_runs(conn, limit: int = 50) -> list[dict]:
    """The fetch history. run() has always written these rows; nothing ever read
    them back for the UI, so a summary that scrolled past in the terminal was the
    only record anyone saw."""
    rows = conn.execute(
        "SELECT id, started_at, kind, fetched, seen, dropped, trashed, kept, errors "
        "FROM runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [
        {"id": r[0], "at": r[1], "kind": r[2], "fetched": r[3], "seen": r[4],
         "dropped": r[5], "trashed": r[6], "kept": r[7], "errors": r[8]}
        for r in rows
    ]


def record_source_error(conn, where: str, message: str) -> int:
    """A fetch failure, where all we have is a message string, not an exception.

    A broken board does not raise into the pool — one bad source must not stop the
    run — so there is no traceback to keep, only the reason the board reported."""
    cur = conn.execute(
        "INSERT INTO errors (where_, kind, message, traceback, notified) "
        "VALUES (?, 'FetchError', ?, '', 0)", (where, str(message)[:500]))
    conn.commit()
    return cur.lastrowid
