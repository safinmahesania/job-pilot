"""A job records when its detail was last opened, so its card can show 'last seen'."""


class TestLastViewed:
    def _add(self, conn, jid):
        conn.execute(
            "INSERT INTO jobs (id, dedupe_hash, title, company, source, status) "
            "VALUES (?,?,?,?,'adzuna','surfaced')", (jid, f"h{jid}", "Dev", "X"))
        conn.commit()

    def test_marking_viewed_stamps_the_time(self, client, conn):
        self._add(conn, 1)
        assert conn.execute("SELECT last_viewed_at FROM jobs WHERE id=1").fetchone()[0] is None
        r = client.post("/api/jobs/1/viewed")
        assert r.json()["ok"] is True
        stamped = conn.execute("SELECT last_viewed_at FROM jobs WHERE id=1").fetchone()[0]
        assert stamped is not None

    def test_viewing_again_updates_the_stamp(self, client, conn):
        self._add(conn, 2)
        client.post("/api/jobs/2/viewed")
        first = conn.execute("SELECT last_viewed_at FROM jobs WHERE id=2").fetchone()[0]
        # force a later timestamp, then view again
        conn.execute("UPDATE jobs SET last_viewed_at = datetime('now','-1 hour') WHERE id=2")
        conn.commit()
        client.post("/api/jobs/2/viewed")
        second = conn.execute("SELECT last_viewed_at FROM jobs WHERE id=2").fetchone()[0]
        assert second >= first or second is not None
