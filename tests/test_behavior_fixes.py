"""Regression tests for four fixes made after real-world use:

  1. .env is loaded from the project root, not the current working directory, so keys
     are found no matter where the app was launched from.
  2. The cover-letter fabrication guard reads skills whether they are a tiered dict or
     a flat list, so a list-form profile no longer flags its own skills as invented.
  3. The feed shows all surfaced jobs when scoring is off, instead of an empty page.
  4. A job can be edited by hand to fix a bad or half-fetched scrape.
"""
import sqlite3

from src import resume_guard


# ── Fix 2: cover-letter guard reads both skill shapes ──

class TestCoverLetterGuardSkillShapes:
    def test_list_form_skills_are_recognised(self):
        # The bug: skills as a flat list were ignored, so the person's own skills read
        # as fabrications and every letter was refused.
        profile = {"identity": {"name": "Sam"}, "skills": ["Python", "Flutter", "SQL"]}
        letter = "I have built systems with Python, Flutter and SQL."
        assert resume_guard.check_cover_letter_prose(letter, profile, "Acme") == []

    def test_dict_form_skills_still_work(self):
        profile = {"identity": {"name": "Sam"},
                   "skills": {"expert": ["Python"], "proficient": ["SQL"]}}
        letter = "Experienced in Python and SQL."
        assert resume_guard.check_cover_letter_prose(letter, profile, "Acme") == []

    def test_real_fabrication_still_caught_with_list_skills(self):
        # The fix must not open the door: a technology that genuinely isn't in the
        # profile is still flagged.
        profile = {"identity": {"name": "Sam"}, "skills": ["Python"]}
        letter = "I have five years of Kubernetes and Rust in production."
        problems = resume_guard.check_cover_letter_prose(letter, profile, "Acme")
        joined = " ".join(problems).lower()
        assert "kubernetes" in joined and "rust" in joined


# ── Fix 3: feed with scoring off ──

class TestFeedWhenScoringOff:
    def _seed(self, conn, scoring_enabled):
        conn.execute("INSERT INTO jobs (dedupe_hash, title, company, status, score) "
                     "VALUES ('u1', 'Job One', 'X', 'surfaced', NULL)")
        conn.execute("INSERT INTO jobs (dedupe_hash, title, company, status, score) "
                     "VALUES ('u2', 'Job Two', 'Y', 'surfaced', NULL)")
        conn.execute("INSERT INTO settings (key, value) VALUES ('scoring_enabled', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (scoring_enabled,))
        conn.commit()

    def test_scoring_off_shows_unscored_jobs_in_feed(self, client, db):
        conn = sqlite3.connect(db)
        self._seed(conn, "0")            # scoring OFF
        conn.close()

        titles = [j["title"] for j in client.get("/api/jobs?tab=feed").json()]
        assert set(titles) == {"Job One", "Job Two"}

    def test_scoring_on_keeps_unscored_out_of_feed(self, client, db):
        conn = sqlite3.connect(db)
        self._seed(conn, "1")            # scoring ON
        conn.close()

        # Unscored jobs don't belong in the ranked feed; they live in the unscored tab.
        assert client.get("/api/jobs?tab=feed").json() == []
        titles = [j["title"] for j in client.get("/api/jobs?tab=unscored").json()]
        assert set(titles) == {"Job One", "Job Two"}


# ── Fix 4: manual job edit ──

class TestManualJobEdit:
    def _one_job(self, db):
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO jobs (dedupe_hash, title, company, description, "
                     "status, score) VALUES ('e1', 'Bad Titl', 'X', 'half...', "
                     "'surfaced', 80)")
        conn.commit()
        jid = conn.execute("SELECT id FROM jobs WHERE dedupe_hash='e1'").fetchone()[0]
        conn.close()
        return jid

    def test_edit_fixes_fields(self, client, db):
        jid = self._one_job(db)
        r = client.patch(f"/api/jobs/{jid}",
                         json={"title": "Senior Dev", "description": "Full text."})
        assert r.status_code == 200
        assert set(r.json()["updated"]) == {"title", "description"}
        assert r.json()["job"]["title"] == "Senior Dev"

    def test_partial_edit_leaves_other_fields_untouched(self, client, db):
        jid = self._one_job(db)
        client.patch(f"/api/jobs/{jid}", json={"title": "Kept Title"})
        r = client.patch(f"/api/jobs/{jid}", json={"company": "Shopify"})
        assert r.json()["job"]["company"] == "Shopify"
        assert r.json()["job"]["title"] == "Kept Title"

    def test_score_is_not_editable(self, client, db):
        jid = self._one_job(db)
        # score isn't a permitted field; sending it changes nothing and isn't reported.
        r = client.patch(f"/api/jobs/{jid}", json={"title": "T", "score": 5})
        assert "score" not in r.json()["updated"]
        # the job is still scored 80 -> still in the feed
        assert any(j["title"] == "T" for j in client.get("/api/jobs?tab=feed").json())

    def test_empty_edit_is_rejected(self, client, db):
        jid = self._one_job(db)
        assert client.patch(f"/api/jobs/{jid}", json={}).status_code == 400

    def test_editing_a_missing_job_is_404(self, client):
        assert client.patch("/api/jobs/999999", json={"title": "x"}).status_code == 404
