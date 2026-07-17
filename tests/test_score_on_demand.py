"""Scoring specific jobs on demand — for unscored imports, without rescoring everything."""
from unittest.mock import patch


class TestScoreOnDemand:
    def test_scores_only_requested_jobs(self, client, conn):
        conn.execute(
            "INSERT INTO jobs (dedupe_hash, title, company, description, status, score) "
            "VALUES ('h1', 'Dev', 'X', 'Python role', 'surfaced', NULL)")
        conn.commit()
        jid = conn.execute("SELECT id FROM jobs WHERE dedupe_hash='h1'").fetchone()[0]

        with patch("src.routes.jobs._rescore_one", return_value=82) as rs:
            r = client.post("/api/jobs/score", json={"job_ids": [jid]})
        assert r.status_code == 200
        body = r.json()
        assert body["requested"] == 1
        assert body["scored"] == 1
        rs.assert_called_once()

    def test_scoring_disabled_returns_403(self, client, conn):
        from src import store
        store.set_setting(conn, "scoring_enabled", "0")
        conn.commit()
        r = client.post("/api/jobs/score", json={"job_ids": [1]})
        assert r.status_code == 403
        store.set_setting(conn, "scoring_enabled", "1")
        conn.commit()
