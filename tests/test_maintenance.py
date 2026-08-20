"""The destructive maintenance operations.

These wipe data, and the reset endpoints behind them were the exact thing the auth
gate exists to protect. They had no test — so nothing checked that a reset deletes
what it promises and, more importantly, PRESERVES what it promises. A reset that also
wiped your settings or config would be a quiet disaster the first time you ran it.
These pin both halves: the right tables are emptied, and settings survive.
"""
import pytest

from src import db as dbmod
from src import maintenance, store

USER = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def seeded(conn, monkeypatch):
    """A database with jobs, history, and a saved setting.

    Seeded on the shared test connection; the maintenance ops under test open their
    own connection via store.connect() (which they close), so that's pointed at the
    same test database with db.connect.
    """
    for dh, title, status in [("h1", "Dev", "surfaced"), ("h2", "Eng", "saved")]:
        jid = conn.execute(
            "INSERT INTO jobs (dedupe_hash, title) VALUES (?, ?) RETURNING id",
            (dh, title)).fetchone()[0]
        conn.execute("INSERT INTO user_jobs (user_id, job_id, status) VALUES (?, ?, ?)",
                     (USER, jid, status))
    conn.execute("INSERT INTO seen (dedupe_hash, decision) VALUES ('h1', 'kept')")
    conn.execute("INSERT INTO runs (kind, kept) VALUES ('fetch', 5)")
    conn.execute("INSERT INTO app_settings (key, value) VALUES ('score_threshold', '80') "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    conn.commit()
    monkeypatch.setattr(store, "connect", dbmod.connect)
    return conn


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _setting(conn, key):
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


class TestResetAllJobs:
    def test_it_wipes_jobs_and_the_seen_log(self, seeded):
        maintenance.reset_all_jobs()

        assert _count(seeded, "jobs") == 0
        assert _count(seeded, "seen") == 0

    def test_it_preserves_your_settings(self, seeded):
        """The whole point of reset_all_jobs over nuclear: your configured threshold
        must survive."""
        maintenance.reset_all_jobs()

        assert _setting(seeded, "score_threshold") == "80"


class TestNuclearReset:
    def test_it_wipes_jobs_seen_history_and_health(self, seeded):
        maintenance.nuclear_reset()

        assert _count(seeded, "jobs") == 0
        assert _count(seeded, "seen") == 0
        assert _count(seeded, "runs") == 0

    def test_it_still_preserves_settings(self, seeded):
        """Even the nuclear option keeps your configuration — it is a data reset, not
        a factory reset. This is promised in its docstring; this holds it to that."""
        maintenance.nuclear_reset()

        assert _setting(seeded, "score_threshold") == "80"


class TestClearRunHistory:
    def test_it_empties_runs_but_leaves_jobs(self, seeded):
        maintenance.clear_run_history()

        assert _count(seeded, "runs") == 0
        assert _count(seeded, "jobs") == 2      # jobs untouched
