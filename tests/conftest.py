"""Shared fixtures.

Two things every test needs, and neither is optional:

  * A database of its own. `store.DB` is bound at import time, so pointing
    `paths.DB_PATH` at a temp file after the fact would do nothing — the module
    already holds the old value. We rebind `store.DB` directly, and build the
    schema through the real `init_db`, so the tests exercise the same migration
    path a user does.

  * A model that never runs. Every LLM call is stubbed. A test that quietly
    reaches for Ollama passes on the author's machine and fails in CI, and a test
    that reaches the network is not a test.
"""
import sqlite3
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os as _os
_os.environ.pop("JOBPILOT_PASSWORD", None)

# Ollama isn't installed in CI and must never be called from a test.
if "ollama" not in sys.modules:
    stub = types.ModuleType("ollama")

    def _refuse(*_args, **_kwargs):
        raise AssertionError("a test tried to call Ollama — stub it")

    stub.chat = _refuse
    sys.modules["ollama"] = stub


#: Every module that binds the database path at import time. `from src.paths
#: import DB_PATH as DB` takes a copy — so patching `paths.DB_PATH` alone leaves
#: these pointing at the real database, and a test would quietly read and write
#: your actual jobs. Each of these must be patched too.
_DB_BINDERS = ["src.store", "src.report", "src.deps"]


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A real, empty JobPilot database, built by the real schema."""
    import importlib
    from src import paths

    path = tmp_path / "test.db"
    monkeypatch.setattr(paths, "DB_PATH", str(path))
    for name in _DB_BINDERS:
        module = importlib.import_module(name)
        monkeypatch.setattr(module, "DB", str(path))

    schema = (ROOT / "data" / "schema.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    conn.commit()
    conn.close()

    return str(path)


@pytest.fixture
def client(db):
    """A TestClient against the app, bound to the per-test database. Shared here so
    every test file can use it, not just test_api.py."""
    from fastapi.testclient import TestClient
    from src import api
    return TestClient(api.app)


@pytest.fixture
def conn(db):
    """An open connection to the test database, closed afterwards."""
    from src import store
    c = store.connect()
    yield c
    c.close()


@pytest.fixture
def profile():
    """A realistic profile, with contact details — so redaction has something to
    redact and a leak has something to leak."""
    return {
        "identity": {"name": "Safin Mahesania", "seniority": "junior",
                     "first_name": "Safin", "last_name": "Mahesania"},
        "contact": {
            "email": "safin@example.com",
            "phone": "+1 514 555 0123",
            "address": "1200 Rue Sainte-Catherine",
            "city": "Montreal",
            "province": "Quebec",
            "postal_code": "H3A 0G4",
            "linkedin": "https://linkedin.com/in/safin",
            "github": "https://github.com/safinmahesania",
        },
        "application": {
            "work_authorized": True,
            "needs_sponsorship": False,
            "willing_to_relocate": True,
            "work_arrangement": "hybrid",
            "willing_to_work_onsite": True,
            "max_days_onsite_per_week": 3,
            "willing_to_commute": True,
            "commute_locations": ["Montreal", "Toronto"],
            "notice_period": "Immediately",
            "years_of_experience": 1,
            "gender": "",
        },
        "custom_answers": [
            {"match": ["criminal", "record"], "answer": "No"},
        ],
        "summary": "MSc CS student building backend systems.",
        "constraints": {"salary_floor": 60000},
        "skills": {"expert": ["Python", "JavaScript"],
                   "proficient": ["FastAPI"], "familiar": ["AWS"]},
        "experience": [{
            "role": "Software Developer Intern", "company": "Acme",
            "start": "2024-05", "end": "2024-08",
            "highlights": ["Cut API latency 40% with a Redis cache"],
        }],
        "projects": [
            {"name": "JobPilot", "tech": ["Python", "FastAPI"],
             "description": "Job automation tool"},
            {"name": "SafeRoute", "tech": ["Python"],
             "description": "Constrained shortest path"},
        ],
        "education": [{"degree": "MSc", "field": "Computer Science",
                       "institution": "Concordia", "end": "2026"}],
    }


#: Every string that must never reach a hosted model in redacted mode.
IDENTIFIERS = [
    "Safin Mahesania",
    "safin@example.com",
    "+1 514 555 0123",
    "1200 Rue Sainte-Catherine",
    "linkedin.com/in/safin",
    "github.com/safinmahesania",
]


@pytest.fixture
def identifiers():
    return list(IDENTIFIERS)


@pytest.fixture
def capture_llm(monkeypatch):
    """Replace llm.generate; record every prompt; return canned answers.

    Usage:
        capture_llm.reply = lambda system, user: ("...", "gemini")
        ... run the code ...
        assert capture_llm.personal_prompts == [...]
    """
    from src import llm

    class Recorder:
        def __init__(self):
            self.calls = []          # (system, user, personal)
            self.reply = lambda system, user: ("ok", "gemini")

        def __call__(self, system, user, personal=False):
            self.calls.append((system, user, personal))
            return self.reply(system, user)

        @property
        def personal_prompts(self):
            return [s + "\n" + u for s, u, personal in self.calls if personal]

        @property
        def all_prompts(self):
            return [s + "\n" + u for s, u, _ in self.calls]

    recorder = Recorder()
    monkeypatch.setattr(llm, "generate", recorder)
    return recorder


@pytest.fixture
def privacy_mode(conn):
    """Set the privacy mode for a test."""
    from src import store

    def _set(mode):
        store.set_setting(conn, "privacy_mode", mode)
        conn.commit()

    return _set


def make_job(**overrides):
    """A valid raw job, before normalisation."""
    job = {
        "source": "greenhouse:shopify",
        "title": "Junior Backend Developer",
        "company": "Shopify",
        "location": "Toronto, Canada",
        "apply_url": "https://boards.greenhouse.io/shopify/jobs/12345",
        "description": "<p>Python, FastAPI, PostgreSQL. New grads welcome.</p>",
        "job_type": "Full-time",
    }
    job.update(overrides)
    return job


@pytest.fixture(autouse=True)
def _unlock_app(monkeypatch):
    """Every test runs against an unlocked app; test_auth_gate opts back in.
    src.notify/src.llm call load_dotenv() at import, so a developer's .env password
    lands back in the environment; clear it before each test."""
    monkeypatch.delenv("JOBPILOT_PASSWORD", raising=False)


@pytest.fixture
def written_profile(tmp_path, monkeypatch):
    """Write a minimal profile.yaml and point the config loaders at it.

    Some endpoints (job import, generation) call load_profile(), which reads a real
    file. That file is gitignored and absent on a clean checkout, so any test that
    exercises those paths needs one written for it — otherwise the test depends on
    whether the developer happens to have a profile.yaml, which is exactly the kind of
    environment coupling CI exists to remove."""
    import yaml as _yaml
    (tmp_path / "profile.yaml").write_text(_yaml.safe_dump({
        "identity": {"name": "Test User", "seniority": "junior"},
        "contact": {"email": "t@example.com"},
        "summary": "A junior developer.",
        "skills": {"expert": ["Flutter", "Python"]},
        "skill_categories": [],
        "constraints": {"locations": ["Remote"]},
        "search": {"role_levels": ["junior"]},
    }), encoding="utf-8")
    (tmp_path / "companies-backup.yaml").write_text(
        _yaml.safe_dump({"companies": []}), encoding="utf-8")

    monkeypatch.setattr("src.paths.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("src.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("src.configio.CONFIG_DIR", tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Clear the rate limiter before every test.

    The limiter keeps its counters in process-global memory, so without this a test
    that makes several generation/import calls would leave those counts sitting there
    for whatever test ran next — and a later test making one more call could tip over a
    limit it never set. That is exactly the kind of order- and timing-dependent failure
    that passes locally and goes red on CI. Resetting per test makes each one start from
    a clean count."""
    try:
        import src.api as api
        api.limiter.reset()
    except Exception:
        pass
    yield
