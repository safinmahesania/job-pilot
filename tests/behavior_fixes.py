"""Regression tests for four fixes made after real-world use:

  1. .env is loaded from the project root, not the current working directory, so keys
     are found no matter where the app was launched from.
  2. The cover-letter fabrication guard reads skills whether they are a tiered dict or
     a flat list, so a list-form profile no longer flags its own skills as invented.
  3. The feed shows all surfaced jobs when scoring is off, instead of an empty page.
  4. A job can be edited by hand to fix a bad or half-fetched scrape.
"""

from src import resume_guard

USER = "00000000-0000-0000-0000-000000000001"



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
        for dh, title, comp in [("u1", "Job One", "X"), ("u2", "Job Two", "Y")]:
            jid = conn.execute(
                "INSERT INTO jobs (dedupe_hash, title, company) VALUES (?,?,?) "
                "RETURNING id", (dh, title, comp)).fetchone()[0]
            conn.execute("INSERT INTO user_jobs (user_id, job_id, status, score) "
                         "VALUES (?, ?, 'surfaced', NULL)", (USER, jid))
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('scoring_enabled', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (scoring_enabled,))
        conn.commit()

    def test_scoring_off_shows_unscored_jobs_in_feed(self, client, conn):
        self._seed(conn, "0")            # scoring OFF

        titles = [j["title"] for j in client.get("/api/jobs?tab=feed").json()]
        assert set(titles) == {"Job One", "Job Two"}

    def test_scoring_on_keeps_unscored_out_of_feed(self, client, conn):
        self._seed(conn, "1")            # scoring ON

        # Unscored jobs don't belong in the ranked feed; they live in the unscored tab.
        assert client.get("/api/jobs?tab=feed").json() == []
        titles = [j["title"] for j in client.get("/api/jobs?tab=unscored").json()]
        assert set(titles) == {"Job One", "Job Two"}


# ── Fix 4: manual job edit ──

class TestManualJobEdit:
    def _one_job(self, conn):
        jid = conn.execute(
            "INSERT INTO jobs (dedupe_hash, title, company, description) "
            "VALUES ('e1', 'Bad Titl', 'X', 'half...') RETURNING id").fetchone()[0]
        conn.execute("INSERT INTO user_jobs (user_id, job_id, status, score) "
                     "VALUES (?, ?, 'surfaced', 80)", (USER, jid))
        conn.commit()
        return jid

    def test_edit_fixes_fields(self, client, conn):
        jid = self._one_job(conn)
        r = client.patch(f"/api/jobs/{jid}",
                         json={"title": "Senior Dev", "description": "Full text."})
        assert r.status_code == 200
        assert set(r.json()["updated"]) == {"title", "description"}
        assert r.json()["job"]["title"] == "Senior Dev"

    def test_partial_edit_leaves_other_fields_untouched(self, client, conn):
        jid = self._one_job(conn)
        client.patch(f"/api/jobs/{jid}", json={"title": "Kept Title"})
        r = client.patch(f"/api/jobs/{jid}", json={"company": "Shopify"})
        assert r.json()["job"]["company"] == "Shopify"
        assert r.json()["job"]["title"] == "Kept Title"

    def test_score_is_not_editable(self, client, conn):
        jid = self._one_job(conn)
        # score isn't a permitted field; sending it changes nothing and isn't reported.
        r = client.patch(f"/api/jobs/{jid}", json={"title": "T", "score": 5})
        assert "score" not in r.json()["updated"]
        # The score the request tried to set was never written. Asserting the job is
        # still in the feed would be a weaker proxy: editing a job legitimately triggers
        # a rescore, so on a machine where scoring actually runs the job can leave the
        # feed for an honest reason and this test would fail for the wrong one.
        score = conn.execute("SELECT score FROM user_jobs WHERE job_id=?",
                             (jid,)).fetchone()[0]
        assert score != 5

    def test_empty_edit_is_rejected(self, client, conn):
        jid = self._one_job(conn)
        assert client.patch(f"/api/jobs/{jid}", json={}).status_code == 400

    def test_editing_a_missing_job_is_404(self, client):
        assert client.patch("/api/jobs/999999", json={"title": "x"}).status_code == 404


