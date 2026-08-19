"""Persistence layer. Talks to Postgres through src.db, which gives the SQLite
call surface the code was written against (? / :name params, hybrid rows,
explicit commits, `with conn:`)."""
from src import db


def connect():
    """Open a Postgres connection (SQLite-compatible surface via src.db).

    Each caller gets its own connection and doesn't share it concurrently across
    the FastAPI threadpool and the scheduler's background thread, so this is safe.
    """
    return db.connect()


def already_seen(conn, dedupe_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM seen WHERE dedupe_hash = ?", (dedupe_hash,)
    ).fetchone()
    return row is not None


def mark_seen(conn, dedupe_hash: str, decision: str, score: float | None = None):
    conn.execute(
        "INSERT INTO seen (dedupe_hash, decision, score) VALUES (?, ?, ?) "
        "ON CONFLICT (dedupe_hash) DO NOTHING",
        (dedupe_hash, decision, score),
    )


def save_job(conn, job: dict):
    """Insert a posting into the SHARED jobs pool.

    In the multi-user model the jobs table holds only what belongs to the posting
    itself: the raw fields plus the 11 extracted ones. The per-user judgement —
    score, status, notes — lives in user_jobs and is written separately when a
    user's feed is built, so nothing here touches it. INSERT … ON CONFLICT DO
    NOTHING keeps the pool deduplicated on dedupe_hash.
    """
    # A job's post URL (source_url) is what "View posting" opens. Some boards only
    # give an apply link; fall back so there's always something to open, and the
    # reverse so "Apply" is never dead either.
    if not (job.get("source_url") or "").strip():
        job["source_url"] = job.get("apply_url") or ""
    if not (job.get("apply_url") or "").strip():
        job["apply_url"] = job.get("source_url") or ""

    # remote is a 0/1 int on the dict but a boolean column in Postgres.
    job["remote"] = bool(job.get("remote"))

    # Extraction fields (src/extract.py) may or may not be on the dict — default
    # every one so the named-parameter INSERT never trips on a missing key.
    for _f in ("work_mode", "seniority_level", "location_detail", "salary_text",
               "benefits", "responsibilities", "requirements", "nice_to_have",
               "tech_stack", "about_company", "instructions", "extracted_at"):
        job.setdefault(_f, None)

    conn.execute(
        """INSERT INTO jobs
           (dedupe_hash, source, source_url, apply_url, title, company,
            location, remote, description, posted_date,
            job_type, deadline, language, salary_min, salary_max,
            work_mode, seniority_level, location_detail, salary_text, benefits,
            responsibilities, requirements, nice_to_have, tech_stack,
            about_company, instructions, extracted_at)
           VALUES (:dedupe_hash, :source, :source_url, :apply_url, :title,
                   :company, :location, :remote, :description, :posted_date,
                   :job_type, :deadline, :language, :salary_min, :salary_max,
                   :work_mode, :seniority_level, :location_detail, :salary_text,
                   :benefits, :responsibilities, :requirements, :nice_to_have,
                   :tech_stack, :about_company, :instructions, :extracted_at)
           ON CONFLICT (dedupe_hash) DO NOTHING""",
        job,
    )


def update_extraction(conn, job_id: int, fields: dict):
    """Write the extracted fields onto an EXISTING job.

    save_job() is INSERT OR IGNORE — it never touches a row that's already there,
    which is correct for fetches but useless for a backfill. This is the other
    half: given a job that already exists, set its extraction columns and stamp
    extracted_at so we know it's been done.

    `fields` is an Extraction.model_dump() — only the extraction columns, nothing
    else, so this can never overwrite a title or a score by accident.
    """
    from src.extract import FIELDS
    allowed = set(FIELDS)
    cols = [k for k in fields if k in allowed]
    if not cols:
        return
    assignments = ", ".join(f"{c} = :{c}" for c in cols)
    params = {c: fields[c] for c in cols}
    params["job_id"] = job_id
    with conn:
        conn.execute(
            f"UPDATE jobs SET {assignments}, extracted_at = datetime('now') "
            "WHERE id = :job_id",
            params,
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
    zero_streak, error_streak, last_ok, alerted = prior or (0, 0, None, False)

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
        alerted = False

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
    conn.executemany("UPDATE source_health SET alerted = true WHERE name = ?",
                     [(n,) for n in names])
    conn.commit()


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO app_settings (key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()


# ── Errors: something you can look back on ─────────────────────────────────────

# The errors table grows one row per failure and nothing ever removed old ones, so
# over a long-running deployment it would climb without bound. A few thousand is far
# more history than anyone reads; past that, the oldest rows are noise. Trim after
# each insert so the table self-limits.
_ERROR_CAP = 2000


def _trim_errors(conn) -> None:
    conn.execute(
        "DELETE FROM errors WHERE id NOT IN ("
        "  SELECT id FROM errors ORDER BY id DESC LIMIT ?)",
        (_ERROR_CAP,),
    )


def record_error(conn, where: str, exc: BaseException,
                 notified: bool = False) -> int:
    """Keep one exception. Returns its id.

    The pipeline used to fail into a print() and an in-memory string the next
    restart erased. This is the difference between "it broke last night" and "it
    broke last night at 2:14, here, with this traceback".
    """
    import traceback as _tb

    tb = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
    row = conn.execute(
        "INSERT INTO errors (where_, kind, message, traceback, notified) "
        "VALUES (?, ?, ?, ?, ?) RETURNING id",
        (where, type(exc).__name__, str(exc), tb, bool(notified)),
    ).fetchone()
    _trim_errors(conn)
    conn.commit()
    return row[0]


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
    row = conn.execute(
        "INSERT INTO errors (where_, kind, message, traceback, notified) "
        "VALUES (?, 'FetchError', ?, '', false) RETURNING id",
        (where, str(message)[:500])).fetchone()
    _trim_errors(conn)
    conn.commit()
    return row[0]
