"""Shared pieces every part of the API layer needs.

api.py grew to a 1300-line god-object: the app, the middleware, and all 70 routes in
one file, with the connection helpers and query constants threaded through them. This
pulls the shared machinery out so the routes can move into their own modules without
each one reaching back into api.py — which would just move the tangle, not undo it.

Nothing here has behaviour of its own; it is the connection helpers, the column list,
the tab-to-SQL map, and the settings accessor that the route modules import.
"""
import sqlite3
from contextlib import contextmanager

from src.paths import DB_PATH as DB
from src import store


# The columns a job row returns to the frontend, in one place so the SELECTs agree.
COLS = ("id, title, company, location, remote, job_type, source, source_url, "
        "apply_url, description, posted_date, deadline, salary_min, salary_max, "
        "score, skills_score, seniority_score, domain_score, rationale, status, "
        "applied_on, notes")

# feed = new/undecided | saved | applied ; dismissed shows nowhere by default.
TAB_WHERE = {
    "feed": "status = 'surfaced'",
    "saved": "status = 'saved'",
    "applied": "status = 'applied'",
    "dismissed": "status = 'dismissed'",
    # Imported jobs whose description couldn't be recovered, so they were never
    # scored. Shown here for manual triage rather than given a number the model had
    # no basis for.
    "unscored": "score IS NULL AND status = 'surfaced'",
}

ALLOWED_STATUS = {"surfaced", "saved", "applied", "dismissed",
                  "interview", "offer", "rejected"}


def _conn():
    """A tuned connection: WAL + busy_timeout, matching store.connect(), so the read
    path and the write path share the file the same way."""
    c = store._tune(sqlite3.connect(DB))
    c.row_factory = sqlite3.Row
    return c


def _db_dep():
    """FastAPI dependency: a connection closed when the request ends.

    Endpoints declare `conn=Depends(_db_dep)` and use conn as before; FastAPI runs the
    code after `yield` when the request finishes — success OR exception — so the
    connection is always closed. This replaces the `conn = _conn() ... conn.close()`
    pattern that leaked on any exception between the two."""
    conn = _conn()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _db():
    """A connection that always closes, even if the body raises. For code that is not
    a request handler and so cannot use the dependency."""
    conn = _conn()
    try:
        yield conn
    finally:
        conn.close()


def _get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default
