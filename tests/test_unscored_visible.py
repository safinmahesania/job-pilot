"""Unscored jobs must appear when you open the Unscored tab, not just be counted.

A frontend bug hid them: isJobView() didn't include 'unscored', so the tab's job list
was never fetched — the count showed (e.g. 18) but the list was empty. This guards the
backend half: /api/jobs?tab=unscored returns the same jobs /api/counts counts.
"""


class TestUnscoredTabReturnsJobs:
    def _add_unscored(self, conn, n):
        for i in range(n):
            conn.execute(
                "INSERT INTO jobs (dedupe_hash, title, company, description, status, score) "
                f"VALUES ('u{i}', 'Dev {i}', 'Co', 'desc', 'surfaced', NULL)")
        conn.commit()

    def test_list_and_count_agree(self, client, conn):
        self._add_unscored(conn, 5)
        count = client.get("/api/counts").json()["unscored"]
        jobs = client.get("/api/jobs?tab=unscored&sort=score&source=all").json()
        assert count == 5
        assert len(jobs) == 5              # the list is not empty
        assert all(j["score"] is None for j in jobs)

    def test_scored_jobs_do_not_appear_in_unscored(self, client, conn):
        conn.execute(
            "INSERT INTO jobs (dedupe_hash, title, company, description, status, score) "
            "VALUES ('scored', 'Dev', 'Co', 'desc', 'surfaced', 85)")
        conn.commit()
        jobs = client.get("/api/jobs?tab=unscored").json()
        assert all(j["score"] is None for j in jobs)
