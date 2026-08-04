"""A job always has both a post URL and an apply URL — each falls back to the other so
neither 'View job' nor 'Apply' is ever a dead button."""

from src import store


class TestUrlFallback:
    def _save(self, conn, **over):
        job = {
            "dedupe_hash": over.get("dedupe_hash", "h1"), "source": "adzuna",
            "source_url": "", "apply_url": "", "title": "Dev", "company": "X",
            "location": "", "remote": 0, "description": "", "posted_date": None,
            "score": 0, "skills_score": 0, "seniority_score": 0, "domain_score": 0,
            "rationale": "", "flags": "", "job_type": None, "deadline": None,
            "language": "en", "salary_min": None, "salary_max": None,
        }
        job.update(over)
        store.save_job(conn, job)
        conn.commit()
        return conn.execute(
            "SELECT source_url, apply_url FROM jobs WHERE dedupe_hash=?",
            (job["dedupe_hash"],)).fetchone()

    def test_empty_source_url_falls_back_to_apply(self, conn):
        src, app = self._save(conn, dedupe_hash="a", source_url="", apply_url="http://apply")
        assert src == "http://apply" and app == "http://apply"

    def test_empty_apply_url_falls_back_to_source(self, conn):
        src, app = self._save(conn, dedupe_hash="b", source_url="http://post", apply_url="")
        assert app == "http://post" and src == "http://post"

    def test_both_present_are_left_alone(self, conn):
        src, app = self._save(conn, dedupe_hash="c", source_url="http://post", apply_url="http://apply")
        assert src == "http://post" and app == "http://apply"
