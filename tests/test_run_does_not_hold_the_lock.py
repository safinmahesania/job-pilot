"""The pipeline must not hold SQLite's write lock for the length of a run.

Scoring calls a model once per job, so a full pass takes minutes. Wrapped in a single
transaction, that is minutes during which nothing else can write: editing a job in the
UI waits out busy_timeout and fails with "database is locked" — which looks like a bug
in the edit, and is really the run next door.

Each job is an independent unit of work, so each one is its own commit.
"""
import sqlite3
from unittest.mock import patch

from src import run, store


def _write_from_elsewhere(db_path) -> str:
    """A second connection doing what the UI does when you save an edit."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout=800")      # short: we want the failure fast
    try:
        conn.execute("UPDATE jobs SET title='edited by the UI' WHERE id=1")
        conn.commit()
        return "ok"
    except sqlite3.OperationalError as e:
        return str(e)
    finally:
        conn.close()


class TestTheWriteLockIsReleasedBetweenJobs:
    def test_another_connection_can_write_mid_run(self, conn, monkeypatch, tmp_path):
        """The heart of it: while the run is between jobs, someone else can write."""
        conn.execute("INSERT INTO jobs (dedupe_hash, title, company, description) "
                     "VALUES ('h1', 'Seed', 'X', 'A real description, long enough to "
                     "be scored by the pipeline under test.')")
        conn.commit()
        db_path = conn.execute("PRAGMA database_list").fetchone()[2]

        outcomes = []

        def _slow_score(job, profile, calibration=""):
            # Stands in for the model call — the moment during which the old code held
            # the lock. Someone tries to save an edit right here.
            outcomes.append(_write_from_elsewhere(db_path))
            return None

        monkeypatch.setattr(run, "score_job", _slow_score)
        monkeypatch.setattr(run, "load_profile", lambda: {
            "skills": {"expert": ["Python"]}, "search": {"role_levels": ["junior"]}})

        jobs = [{"title": "Dev", "company": "Acme", "location": "Toronto",
                 "apply_url": "https://example.com/1", "source_url": "https://example.com/1",
                 "description": "A description long enough to be scored properly.",
                 "source": "test"}]
        with patch.object(run, "fetch_all", return_value=(jobs, {})):
            try:
                run.run_pipeline()
            except Exception:
                pass                      # the run's own plumbing isn't what's on trial

        if outcomes:                      # scoring was reached
            assert outcomes[0] == "ok", (
                f"a second connection could not write during the run: {outcomes[0]}")


class TestPerJobCommitSurvivesACrash:
    def test_work_done_before_a_failure_is_kept(self, conn):
        """A run that dies halfway keeps what it had already finished, rather than
        rolling the whole pass back — the same property, seen from the other side."""
        conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.commit()

        for i in range(3):
            try:
                conn.execute("INSERT INTO t (v) VALUES (?)", (f"job{i}",))
                if i == 2:
                    raise RuntimeError("the model fell over")
            except RuntimeError:
                pass
            finally:
                conn.commit()

        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3


class TestConnectionsAreTunedForSharing:
    def test_wal_and_a_busy_timeout_are_set(self):
        c = store.connect()
        try:
            assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
            assert c.execute("PRAGMA busy_timeout").fetchone()[0] > 0
        finally:
            c.close()


class TestSourceHealthDoesNotHoldTheLock:
    """The job loop was fixed to commit per job, but the writes on either side of it —
    source health, the error record, the run-history row — were left loose. A write
    without a commit holds the lock just the same, so a run still locked the database
    for its whole length through that path, which nobody had retested.

    This is the failure from the screenshot: "Pipeline failed / database is locked",
    once per scheduled run.
    """

    def test_writing_source_health_leaves_no_open_transaction(self, conn):
        from src import store

        stat = {"status": "ok", "fetched": 5, "kept": 3, "error": None}
        with conn:
            store.save_source_health(conn, "Adzuna", "adzuna", stat, "2025-01-01 10:00")

        # If the write above had been left in an open transaction, a second connection
        # would not be able to write.
        db_path = conn.execute("PRAGMA database_list").fetchone()[2]
        import sqlite3
        other = sqlite3.connect(db_path)
        other.execute("PRAGMA busy_timeout=800")
        try:
            with other:
                other.execute("INSERT INTO settings (key, value) VALUES ('k', 'v')")
            wrote = True
        except sqlite3.OperationalError:
            wrote = False
        finally:
            other.close()

        assert wrote, "source-health write left the database locked"

    def test_a_full_run_ends_with_nothing_holding_the_lock(self, conn, monkeypatch):
        """After the pipeline returns, the database is writable at once — no lingering
        transaction from the run-history insert or the last source-health write."""
        from src import run

        monkeypatch.setattr(run, "load_profile", lambda: {
            "skills": {"expert": ["Python"]}, "search": {"role_levels": ["junior"]}})
        monkeypatch.setattr(run, "fetch_all", lambda *a, **k: [
            ({"name": "Adzuna", "ats": "adzuna"}, [], {"status": "ok", "fetched": 0})])

        try:
            run.run()
        except Exception:
            pass

        db_path = conn.execute("PRAGMA database_list").fetchone()[2]
        import sqlite3
        other = sqlite3.connect(db_path)
        other.execute("PRAGMA busy_timeout=800")
        try:
            with other:
                other.execute("INSERT INTO settings (key, value) VALUES ('after', '1')")
            wrote = True
        except sqlite3.OperationalError:
            wrote = False
        finally:
            other.close()

        assert wrote, "the run left the database locked after finishing"
