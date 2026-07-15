"""WAL, so the scheduler and the UI can share one database.

The pipeline runs in a background thread and writes; you browse the feed in a request
thread and read. Under SQLite's default rollback journal a writer blocks readers for
the length of each write, so a fetch running while you click around surfaces as
"database is locked" — intermittent, timing-dependent, and exactly the kind of bug
that does not show up until the app is deployed and the scheduler is running for real.

WAL lets readers keep reading while a writer writes. These tests pin that on so a
future refactor of the connection helpers cannot quietly drop it.
"""
import sqlite3
import threading

import pytest

from src import store


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "wal.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        open("data/schema.sql", encoding="utf-8").read())
    conn.commit()
    conn.close()

    monkeypatch.setattr("src.paths.DB_PATH", path)
    monkeypatch.setattr(store, "DB", path)
    return path


class TestTheConnectionIsTuned:
    def test_store_connect_is_in_wal_mode(self, db):
        conn = store.connect()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()

        assert mode.lower() == "wal"

    def test_store_connect_waits_on_a_lock_instead_of_failing(self, db):
        """busy_timeout means a call that hits a lock waits, rather than raising
        'database is locked' the instant it cannot get in."""
        conn = store.connect()
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.close()

        assert timeout >= 1000

    def test_the_api_connection_is_tuned_the_same_way(self, db):
        """The read path and the write path must agree. If the UI's own connections
        fell back to the default journal, they would re-introduce the locking that
        WAL removes."""
        import src.api as api

        conn = api._conn()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()

        assert mode.lower() == "wal"


class TestReadingWhileWriting:
    def test_a_reader_is_not_blocked_by_an_open_write(self, db):
        """The whole point. A writer mid-transaction must not lock out a reader —
        which, under the old default journal, it did."""
        writer = store.connect()
        writer.execute("BEGIN")
        writer.execute(
            "INSERT INTO jobs (dedupe_hash, title) VALUES ('h1', 'in progress')")

        reader = store.connect()
        try:
            # This line raised "database is locked" before WAL.
            count = reader.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        finally:
            writer.commit()
            reader.close()
            writer.close()

        # The reader sees the last committed state (0), not the pending write.
        assert count == 0

    def test_the_committed_write_is_then_visible(self, db):
        writer = store.connect()
        writer.execute(
            "INSERT INTO jobs (dedupe_hash, title) VALUES ('h2', 'done')")
        writer.commit()

        reader = store.connect()
        count = reader.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        reader.close()
        writer.close()

        assert count == 1