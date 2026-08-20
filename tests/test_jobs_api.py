
USER = "00000000-0000-0000-0000-000000000001"

class TestSearchOnlyFeedAndSaved:
    """The extension picker should only offer jobs in play — surfaced or saved — never
    dismissed or applied ones."""

    def _add(self, conn, jid, status, title="Dev Role"):
        conn.execute(
            "INSERT INTO jobs (id, dedupe_hash, title, company, source) "
            "OVERRIDING SYSTEM VALUE VALUES (?,?,?,?,'adzuna')",
            (jid, f"h{jid}", title, "Acme"))
        conn.execute(
            "INSERT INTO user_jobs (user_id, job_id, status) VALUES (?,?,?)",
            (USER, jid, status))
        conn.commit()

    def test_dismissed_and_applied_are_excluded(self, client, conn):
        self._add(conn, 1, "surfaced")
        self._add(conn, 2, "saved")
        self._add(conn, 3, "dismissed")
        self._add(conn, 4, "applied")
        got = {j["id"] for j in client.get("/api/jobs/search?q=Dev").json()}
        assert got == {1, 2}

    def test_saved_sorts_before_surfaced(self, client, conn):
        self._add(conn, 5, "surfaced")
        self._add(conn, 6, "saved")
        ids = [j["id"] for j in client.get("/api/jobs/search?q=Dev").json()]
        assert ids.index(6) < ids.index(5)
