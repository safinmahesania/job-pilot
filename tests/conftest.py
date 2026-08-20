"""Shared fixtures for the Postgres-era test suite.

The app is multi-user Postgres now, so tests run against a REAL Postgres — never
SQLite, and never your production database. Point TEST_DATABASE_URL at an empty
throwaway database (a local Postgres, or a scratch Supabase project); if it isn't
set, the database tests skip rather than touch anything real.

Two guarantees every test gets:

  * A clean, isolated database. The schema is applied once per session; before each
    test every data table is truncated and a canonical test user + profile are
    re-seeded, so tests can't leak into one another.

  * A model that never runs. Ollama is stubbed at import; anything that reaches for a
    real LLM must stub it explicitly (score_job, extract, etc.).
"""
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.pop("JOBPILOT_PASSWORD", None)

# Ollama isn't installed in CI and must never be called from a test.
if "ollama" not in sys.modules:
    _stub = types.ModuleType("ollama")

    def _refuse(*_a, **_k):
        raise AssertionError("a test tried to call Ollama — stub it")

    _stub.chat = _refuse
    sys.modules["ollama"] = _stub


TEST_DB = os.environ.get("TEST_DATABASE_URL")

#: The canonical test user. A second one is available for isolation tests.
USER = "00000000-0000-0000-0000-000000000001"
USER2 = "00000000-0000-0000-0000-000000000002"

#: Tables cleared before every test (users/app_settings are kept and re-seeded).
_DATA_TABLES = [
    "user_jobs", "materials", "application_answers", "notifications",
    "user_profiles", "user_settings", "jobs", "seen", "source_health",
    "runs", "errors", "llm_usage",
]

_AUTH_SHIM = """
create schema if not exists auth;
create table if not exists auth.users (id uuid primary key default gen_random_uuid(), email text);
create or replace function auth.uid() returns uuid language sql stable as
  $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
do $$ begin create role authenticated; exception when duplicate_object then null; end $$;
do $$ begin create role anon; exception when duplicate_object then null; end $$;
"""


def _apply_schema(conn):
    """Bring the test database up to the current schema (idempotent)."""
    conn.execute(_AUTH_SHIM)
    schema = (ROOT / "data" / "jobpilot_schema_postgres.sql").read_text(encoding="utf-8")
    conn.execute(schema)
    conn.commit()


def _seed_users(conn):
    for uid, email in ((USER, "test1@example.com"), (USER2, "test2@example.com")):
        conn.execute(
            "INSERT INTO auth.users (id, email) VALUES (?, ?) ON CONFLICT DO NOTHING",
            (uid, email))
        conn.execute(
            "INSERT INTO public.users (id, email, is_admin) VALUES (?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET is_admin=excluded.is_admin",
            (uid, email, uid == USER))
    conn.commit()


@pytest.fixture(scope="session")
def _session_conn():
    if not TEST_DB:
        pytest.skip("set TEST_DATABASE_URL to run the database tests")
    os.environ["DATABASE_URL"] = TEST_DB          # app code connects here
    from src import db
    conn = db.connect()
    try:
        # Is the schema there already? If not, apply it.
        has = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='user_jobs'").fetchone()[0]
        if not has:
            _apply_schema(conn)
        yield conn
    finally:
        conn.close()


@pytest.fixture
def db(_session_conn):
    """A clean, isolated database with the two test users + user1's profile seeded."""
    conn = _session_conn
    conn.execute("TRUNCATE " + ", ".join(_DATA_TABLES) + " RESTART IDENTITY CASCADE")
    conn.commit()
    _seed_users(conn)
    # A basic profile for USER so prefilter/scoring paths have something to read.
    conn.execute(
        "INSERT INTO user_profiles (user_id, profile) VALUES (?, ?::jsonb) "
        "ON CONFLICT (user_id) DO UPDATE SET profile=excluded.profile",
        (USER, '{"search": {"titles": ["Backend Developer"]}, '
               '"constraints": {"locations": ["Canada", "Remote"]}}'))
    conn.commit()
    yield conn


@pytest.fixture
def user():
    return USER


@pytest.fixture
def user2():
    return USER2
