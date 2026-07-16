"""The database connection must survive being used across threads.

FastAPI runs sync endpoint dependencies in a threadpool: a connection created in one
worker thread is used by the endpoint in another and closed in a third. SQLite's default
(check_same_thread=True) rejects that with "SQLite objects created in a thread can only
be used in that same thread", which under real uvicorn turned every endpoint into a 500
— while the single-threaded test client sailed through, so nothing caught it.

These tests exercise the connection from a *different* thread than the one that created
it, which is exactly what the test client does not do, so a regression here would be
caught before it reaches a running server.
"""
import sqlite3
import threading



def _use_from_another_thread(make_conn):
    """Create a connection in this thread, use it in a second thread, report any error."""
    conn = make_conn()
    box = {}

    def worker():
        try:
            conn.execute("SELECT 1").fetchone()
            box["ok"] = True
        except Exception as e:                      # pragma: no cover - failure path
            box["error"] = str(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    conn.close()
    return box


def test_deps_connection_crosses_threads(db, monkeypatch):
    import src.deps as deps
    monkeypatch.setattr(deps, "DB", db)
    box = _use_from_another_thread(deps._conn)
    assert box.get("ok"), f"connection failed across threads: {box.get('error')}"


def test_store_connection_crosses_threads(db, monkeypatch):
    import src.store as store
    monkeypatch.setattr(store, "DB", db)
    box = _use_from_another_thread(store.connect)
    assert box.get("ok"), f"connection failed across threads: {box.get('error')}"


def test_endpoints_work_when_the_dep_runs_off_thread(client, db):
    # A belt-and-braces check at the HTTP layer: hit a few of the endpoints that were
    # 500-ing (each one opens a connection through the dependency) and confirm they now
    # return cleanly. Under the real server these ran on threadpool workers; here we at
    # least prove the dependency + query + close cycle succeeds end to end.
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO jobs (dedupe_hash, title, company, status, score) "
                 "VALUES ('t', 'Dev', 'X', 'surfaced', 90)")
    conn.commit()
    conn.close()

    for path in ("/api/counts", "/api/stats", "/api/settings", "/api/schedule",
                 "/api/sources", "/api/followups", "/api/health/assess"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
