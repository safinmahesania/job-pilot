"""The destructive maintenance operations.

These wipe data, and the reset endpoints behind them were the exact thing the auth
gate exists to protect. They had no test — so nothing checked that a reset deletes
what it promises and, more importantly, PRESERVES what it promises. A reset that also
wiped your settings or config would be a quiet disaster the first time you ran it.
These pin both halves: the right tables are emptied, and settings survive.
"""
import sqlite3

import pytest

from src import maintenance, store


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """A database with jobs, history, and a saved setting."""
    path = str(tmp_path / "maint.db")
    conn = sqlite3.connect(path)
    conn.executescript(open("data/schema.sql", encoding="utf-8").read())
    # Some jobs, some history, and a setting the user configured.
    conn.execute("INSERT INTO jobs (dedupe_hash, title, status) "
                 "VALUES ('h1', 'Dev', 'surfaced')")
    conn.execute("INSERT INTO jobs (dedupe_hash, title, status) "
                 "VALUES ('h2', 'Eng', 'saved')")
    conn.execute("INSERT INTO seen (dedupe_hash, decision) VALUES ('h1', 'kept')")
    conn.execute("INSERT INTO runs (kind, kept) VALUES ('fetch', 5)")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('score_threshold', '80')")
    conn.commit()
    conn.close()

    monkeypatch.setattr("src.paths.DB_PATH", path)
    monkeypatch.setattr(store, "DB", path)
    return path


def _count(path, table):
    conn = sqlite3.connect(path)
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return n


def _setting(path, key):
    conn = sqlite3.connect(path)
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row[0] if row else None


class TestResetAllJobs:
    def test_it_wipes_jobs_and_the_seen_log(self, seeded_db):
        maintenance.reset_all_jobs()

        assert _count(seeded_db, "jobs") == 0
        assert _count(seeded_db, "seen") == 0

    def test_it_preserves_your_settings(self, seeded_db):
        """The whole point of reset_all_jobs over nuclear: your configured threshold
        must survive."""
        maintenance.reset_all_jobs()

        assert _setting(seeded_db, "score_threshold") == "80"


class TestNuclearReset:
    def test_it_wipes_jobs_seen_history_and_health(self, seeded_db):
        maintenance.nuclear_reset()

        assert _count(seeded_db, "jobs") == 0
        assert _count(seeded_db, "seen") == 0
        assert _count(seeded_db, "runs") == 0

    def test_it_still_preserves_settings(self, seeded_db):
        """Even the nuclear option keeps your configuration — it is a data reset, not
        a factory reset. This is promised in its docstring; this holds it to that."""
        maintenance.nuclear_reset()

        assert _setting(seeded_db, "score_threshold") == "80"


class TestClearRunHistory:
    def test_it_empties_runs_but_leaves_jobs(self, seeded_db):
        maintenance.clear_run_history()

        assert _count(seeded_db, "runs") == 0
        assert _count(seeded_db, "jobs") == 2      # jobs untouched
