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

# Hard safety guard: never let the test DB be the production database. If
# TEST_DATABASE_URL is unset, or it matches DATABASE_URL, or it points at a
# hosted Supabase host, refuse — because the fixtures TRUNCATE every data table
# and would wipe real data. Use a local or throwaway Postgres for tests.
_PROD_URL = os.environ.get("DATABASE_URL", "")


def _looks_like_production(url: str) -> bool:
    if not url:
        return False
    if _PROD_URL and url.strip() == _PROD_URL.strip():
        return True
    lowered = url.lower()
    # Supabase's own DB hosts; a scratch project should use a DB you created for tests.
    return "supabase.co" in lowered or "supabase.com" in lowered or "pooler.supabase" in lowered

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
    if _looks_like_production(TEST_DB):
        pytest.exit(
            "REFUSING TO RUN: TEST_DATABASE_URL looks like a production/Supabase "
            "database. The test fixtures TRUNCATE every table. Point "
            "TEST_DATABASE_URL at a LOCAL or throwaway Postgres, never your real DB.",
            returncode=2)
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
    # A prior test may have left the shared session connection in a failed-transaction
    # state (a bad query with no rollback poisons every later query as
    # InFailedSqlTransaction). Clear it before this test's setup runs.
    try:
        conn.rollback()
    except Exception:
        pass
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


@pytest.fixture
def profile():
    """A complete candidate profile dict, as score_job / autofill / generation expect."""
    return {
        "summary": "Backend developer with 4 years building Python APIs.",
        "identity": {"name": "Safin Mahesania", "first_name": "Safin",
                     "last_name": "Mahesania", "seniority": "mid"},
        "seniority": "mid",
        "contact": {"email": "safin@example.com", "phone": "+1 514 555 0123",
                    "address": "1 Test St", "city": "Montreal", "province": "QC"},
        "application": {"gender": "Prefer not to say", "authorized_to_work": "Yes"},
        "skills": {"core": ["Python", "FastAPI", "PostgreSQL"],
                   "familiar": ["Docker", "React"]},
        "skill_categories": {"languages": ["Python"], "frameworks": ["FastAPI"]},
        "experience": [{"title": "Backend Developer", "company": "Acme",
                        "start": "2021", "end": "present",
                        "highlights": ["Built REST APIs"]}],
        "projects": [{"name": "JobPilot", "summary": "A job-hunting tool"}],
        "education": [{"school": "Concordia", "degree": "MSc CS", "end": "2026"}],
        "search": {"titles": ["Backend Developer"]},
        "constraints": {"locations": ["Canada", "Remote"]},
    }


@pytest.fixture
def identifiers():
    """The PII strings that must never leak into a hosted-model prompt."""
    return ["Safin Mahesania", "Safin", "safin@example.com", "+1 514 555 0123"]


@pytest.fixture
def capture_llm(monkeypatch):
    """Intercept every llm.generate call: record it and return a scripted reply.

    Tests set ``capture_llm.reply`` to a ``(system, user) -> (text, provider)``
    callable (or one that raises, to simulate a dead provider). The prompts and the
    (system, user, personal) tuples are captured so a test can assert on exactly what
    would have gone to a model.
    """
    from src import llm

    class Capture:
        def __init__(self):
            self.reply = lambda system, user: (
                '{"skills_score": 80, "seniority_score": 80, "domain_score": 80, '
                '"overall": 80, "rationale": "ok"}', "cerebras")
            self.calls = []          # list of (system, user, personal)
            self.all_prompts = []    # every system and user string seen

        def __call__(self, system, user, personal=False):
            self.calls.append((system, user, personal))
            self.all_prompts.append(system)
            self.all_prompts.append(user)
            return self.reply(system, user)

    cap = Capture()
    monkeypatch.setattr(llm, "generate", cap)
    return cap


@pytest.fixture
def privacy_mode(monkeypatch):
    """Set the effective privacy mode ('redacted' | 'local' | 'full') for a test."""
    from src import llm

    def _set(mode):
        monkeypatch.setattr(llm, "privacy_mode", lambda: mode)
    return _set


@pytest.fixture
def written_profile(db, profile):
    """Persist the test user's profile so profile-dependent endpoints have one."""
    import json
    db.execute(
        "INSERT INTO user_profiles (user_id, profile) VALUES (?, ?::jsonb) "
        "ON CONFLICT (user_id) DO UPDATE SET profile = excluded.profile",
        (USER, json.dumps(profile)))
    db.commit()
    return profile


# ── compatibility helpers for the ported single-user suite ───────────────────
#
# The legacy tests were written against a single-user SQLite app. These provide the
# same names they reach for, adapted to the multi-user Postgres model: `conn` is just
# the clean test connection, `client` is a TestClient already authenticated as the
# test user, and `make_job` builds a raw job dict for the normalize/adapter tests.

@pytest.fixture
def conn(db):
    """Alias: many legacy tests take a `conn` fixture. Same clean test connection."""
    return db


def make_job(**overrides):
    """A raw job dict with sensible defaults, for the pure-logic tests (normalize,
    adapters, scoring prompts). Override any field via keyword."""
    job = {
        "title": "Backend Developer",
        "company": "Acme Corp",
        "location": "Toronto, Canada",
        "description": "We are hiring a backend developer to build APIs and services.",
        "url": "https://x/1",
        "apply_url": "https://x/1",
        "source_url": "https://x/1",
        "source": "test",
        "job_type": "Full-time",
        "salary": None,
        "salary_min": None,
        "salary_max": None,
        "posted_date": None,
        "remote": 0,
        "dedupe_hash": "hash-1",
    }
    job.update(overrides)
    return job


@pytest.fixture
def client(db, monkeypatch):
    """A TestClient authenticated as the test user.

    The old app was open on localhost; every route is behind a Supabase JWT now, so
    the fixture overrides the auth dependencies to resolve to USER (an admin) instead
    of requiring a real token. Routes still read/write that user's own rows.
    """
    from fastapi.testclient import TestClient

    from src import api
    from src.auth import current_user_id
    from src.deps import require_admin

    api.app.dependency_overrides[current_user_id] = lambda: USER
    api.app.dependency_overrides[require_admin] = lambda: USER
    try:
        yield TestClient(api.app)
    finally:
        api.app.dependency_overrides.pop(current_user_id, None)
        api.app.dependency_overrides.pop(require_admin, None)
