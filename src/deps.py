"""Shared pieces every part of the API layer needs.

api.py grew to a 1300-line god-object: the app, the middleware, and all 70 routes in
one file, with the connection helpers and query constants threaded through them. This
pulls the shared machinery out so the routes can move into their own modules without
each one reaching back into api.py — which would just move the tangle, not undo it.

Nothing here has behaviour of its own; it is the connection helpers, the column list,
the tab-to-SQL map, and the settings accessor that the route modules import.
"""
import os
from contextlib import contextmanager

from fastapi import Depends, HTTPException

from src import db, store  # noqa: F401  (store re-exported for callers)
from src.auth import current_user_id

from slowapi import Limiter
from slowapi.util import get_remote_address
from src.paths import RATE_LIMIT_DEFAULT

# The rate limiter lives here so both api.py (which wires it to the app and its error
# handler) and the route modules (which decorate the expensive endpoints with
# @limiter.limit) can share the one instance. Keyed by client address; the per-route
# limits are applied where the routes are defined.
def _rate_key(request):
    """Rate-limit key. Behind a reverse proxy the socket address is the proxy's, so
    every client would share one bucket. When TRUST_PROXY is set (you ARE behind a
    known proxy), key on the original client from X-Forwarded-For instead. Off by
    default: trusting that header from a direct client would let anyone spoof a key."""
    if os.environ.get("TRUST_PROXY"):
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=_rate_key, default_limits=[RATE_LIMIT_DEFAULT])


# The columns a feed row returns to the frontend, split by where they now live.
# Posting + extracted fields belong to the shared jobs row (aliased j.); the
# per-user judgement — score, status, notes — lives in user_jobs (aliased uj.).
JOB_COLS = ("j.id, j.title, j.company, j.location, j.remote, j.job_type, j.source, "
            "j.source_url, j.apply_url, j.description, j.posted_date, j.deadline, "
            "j.salary_min, j.salary_max, j.language, j.fetched_at, "
            # Structured fields lifted from the description by src/extract.py.
            "j.work_mode, j.seniority_level, j.location_detail, j.salary_text, "
            "j.benefits, j.responsibilities, j.requirements, j.nice_to_have, "
            "j.tech_stack, j.about_company, j.instructions, j.extracted_at")

USER_COLS = ("uj.score, uj.skills_score, uj.seniority_score, uj.domain_score, "
             "uj.rationale, uj.status, uj.applied_on, uj.notes, uj.last_viewed_at, "
             "uj.served_at")

# What a feed SELECT returns: the posting joined to this user's judgement of it.
FEED_COLS = JOB_COLS + ", " + USER_COLS

# feed = new/undecided | saved | applied ; dismissed shows nowhere by default.
# All reference uj.* because status/score are per-user now; the feed's threshold
# filter is added in the route (it depends on the user's setting).
TAB_WHERE = {
    "feed": "uj.status = 'surfaced'",
    "saved": "uj.status = 'saved'",
    "applied": "uj.status = 'applied'",
    "dismissed": "uj.status = 'dismissed'",
    # Imported/served jobs that were never scored — shown for manual triage.
    "unscored": "uj.score IS NULL AND uj.status = 'surfaced'",
}

ALLOWED_STATUS = {"surfaced", "saved", "applied", "dismissed",
                  "interview", "offer", "rejected"}


def _conn():
    """A Postgres connection with the SQLite-compatible surface (src.db).

    Each request/worker gets its own connection and never shares it concurrently,
    matching the previous per-connection model."""
    return db.connect()


def _db_dep():
    """FastAPI dependency: a connection closed when the request ends.

    Endpoints declare `conn=Depends(_db_dep)` and use conn as before; FastAPI runs the
    code after `yield` when the request finishes — success OR exception — so the
    connection is always closed. This replaces the `conn = _conn() ... conn.close()`
    pattern that leaked on any exception between the two."""
    conn = db.acquire()
    try:
        yield conn
    finally:
        db.release(conn)


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
    """A GLOBAL/admin setting (app_settings)."""
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def _get_user_setting(conn, user_id, key, default=None):
    """A PER-USER setting (user_settings)."""
    row = conn.execute(
        "SELECT value FROM user_settings WHERE user_id=? AND key=?",
        (user_id, key)).fetchone()
    return row[0] if row else default


def _set_user_setting(conn, user_id, key, value):
    conn.execute(
        "INSERT INTO user_settings (user_id, key, value) VALUES (?,?,?) "
        "ON CONFLICT (user_id, key) DO UPDATE SET value=excluded.value",
        (user_id, key, str(value)))
    conn.commit()


def _user_profile(conn, user_id) -> dict:
    row = conn.execute("SELECT profile FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
    if not row or not row[0]:
        return {}
    p = row[0]
    if isinstance(p, dict):
        return p
    import json
    try:
        return json.loads(p)
    except Exception:
        return {}


def _user_threshold(conn, user_id) -> int:
    """This user's feed score cutoff: their own setting, else the global default."""
    v = _get_user_setting(conn, user_id, "score_threshold")
    if v is None:
        v = _get_setting(conn, "default_score_threshold", 70)
    return int(v)


def require_admin(user_id: str = Depends(current_user_id), conn=Depends(_db_dep)) -> str:
    """Dependency for admin-only endpoints: 403 unless the caller's is_admin is set."""
    row = conn.execute("SELECT is_admin FROM users WHERE id=?", (user_id,)).fetchone()
    if not row or not row[0]:
        raise HTTPException(403, "Admin only")
    return user_id
