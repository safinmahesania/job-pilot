"""Unscored jobs must appear when you open the Unscored tab, not just be counted.

A frontend bug hid them: isJobView() didn't include 'unscored', so the tab's job list
was never fetched — the count showed (e.g. 18) but the list was empty. This guards the
backend half: /api/jobs?tab=unscored returns the same jobs /api/counts counts.
"""

USER = "00000000-0000-0000-0000-000000000001"

class TestUnscoredTabReturnsJobs:
    def _add_unscored(self, conn, n):
        for i in range(n):
            jid = conn.execute(
                "INSERT INTO jobs (dedupe_hash, title, company, description) "
                f"VALUES ('u{i}', 'Dev {i}', 'Co', 'desc') RETURNING id").fetchone()[0]
            conn.execute(
                "INSERT INTO user_jobs (user_id, job_id, status, score) "
                "VALUES (?, ?, 'surfaced', NULL)", (USER, jid))
        conn.commit()

    def test_list_and_count_agree(self, client, conn):
        self._add_unscored(conn, 5)
        count = client.get("/api/counts").json()["unscored"]
        jobs = client.get("/api/jobs?tab=unscored&sort=score&source=all").json()
        assert count == 5
        assert len(jobs) == 5              # the list is not empty
        assert all(j["score"] is None for j in jobs)

    def test_scored_jobs_do_not_appear_in_unscored(self, client, conn):
        jid = conn.execute(
            "INSERT INTO jobs (dedupe_hash, title, company, description) "
            "VALUES ('scored', 'Dev', 'Co', 'desc') RETURNING id").fetchone()[0]
        conn.execute(
            "INSERT INTO user_jobs (user_id, job_id, status, score) "
            "VALUES (?, ?, 'surfaced', 85)", (USER, jid))
        conn.commit()
        jobs = client.get("/api/jobs?tab=unscored").json()
        assert all(j["score"] is None for j in jobs)
