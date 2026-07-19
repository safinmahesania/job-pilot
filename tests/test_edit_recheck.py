"""An edited job goes back through the filters, not just the scorer.

A job fetched with half a description was judged on half a description. Once the real
posting is pasted in, things that were unknowable become knowable — that it is in
Austin, that it is a staff role — and a job that looked fine on its title turns out
not to be one to keep. A fetch run drops such a job; an edit used to leave it sitting
in the feed with a fresh score and a constraint it plainly fails.
"""
from unittest.mock import patch


CANADIAN_JUNIOR = {
    "constraints": {"locations": ["remote", "toronto", "ontario", "canada"]},
    "search": {"role_levels": ["junior", "intern"]},
    "skills": {"expert": ["Python"]},
}


def _job(conn, **cols):
    base = {"dedupe_hash": "r1", "title": "Developer", "company": "X",
            "location": "Toronto, ON", "description": "Python work.", "status": "surfaced"}
    base.update(cols)
    keys = ", ".join(base)
    marks = ", ".join("?" * len(base))
    conn.execute(f"INSERT INTO jobs ({keys}) VALUES ({marks})", list(base.values()))
    conn.commit()
    return conn.execute("SELECT id FROM jobs WHERE dedupe_hash=?",
                        (base["dedupe_hash"],)).fetchone()[0]


class TestRecheck:
    def test_a_job_that_still_fits_is_left_alone(self, client, conn):
        jid = _job(conn)
        with patch("src.config.load_profile", return_value=CANADIAN_JUNIOR):
            body = client.post(f"/api/jobs/{jid}/recheck").json()
        assert body["verdict"] == "ok"
        status = conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0]
        assert status == "surfaced"

    def test_a_job_outside_your_locations_is_dismissed(self, client, conn):
        jid = _job(conn, dedupe_hash="r2", location="Austin, TX")
        with patch("src.config.load_profile", return_value=CANADIAN_JUNIOR):
            body = client.post(f"/api/jobs/{jid}/recheck").json()
        assert body["verdict"] == "dismissed"
        assert "Austin" in body["reason"]          # says which rule, and with what value
        status = conn.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0]
        assert status == "dismissed"

    def test_the_reason_is_in_words_not_a_code(self, client, conn):
        jid = _job(conn, dedupe_hash="r3", location="Berlin, Germany")
        with patch("src.config.load_profile", return_value=CANADIAN_JUNIOR):
            reason = client.post(f"/api/jobs/{jid}/recheck").json()["reason"]
        assert "location" in reason and len(reason.split()) > 4

    def test_no_profile_means_unchecked_not_passed(self, client, conn):
        """Claiming a job passed filters that were never applied would be a lie."""
        jid = _job(conn, dedupe_hash="r4")
        with patch("src.config.load_profile", return_value={}):
            body = client.post(f"/api/jobs/{jid}/recheck").json()
        assert body["verdict"] == "unchecked"

    def test_unknown_job_is_404(self, client):
        assert client.post("/api/jobs/999999/recheck").status_code == 404


class TestDeferredScoring:
    def test_defer_skips_the_inline_rescore_and_says_so(self, client, conn):
        jid = _job(conn, dedupe_hash="r5")
        with patch("src.routes.jobs._rescore_one") as rs:
            body = client.patch(f"/api/jobs/{jid}?defer=true",
                                json={"description": "A longer, real description."}).json()
        rs.assert_not_called()
        assert body["needs_reprocess"] is True
        assert body["rescored"] is None

    def test_without_defer_the_old_behaviour_is_unchanged(self, client, conn):
        jid = _job(conn, dedupe_hash="r6")
        with patch("src.routes.jobs._rescore_one", return_value=71) as rs:
            body = client.patch(f"/api/jobs/{jid}",
                                json={"description": "A longer, real description."}).json()
        rs.assert_called_once()
        assert body["rescored"] == 71
        assert body["needs_reprocess"] is False

    def test_an_edit_that_cannot_change_the_score_needs_no_reprocess(self, client, conn):
        jid = _job(conn, dedupe_hash="r7")
        body = client.patch(f"/api/jobs/{jid}?defer=true",
                            json={"apply_url": "https://example.com/2"}).json()
        assert body["needs_reprocess"] is False


class TestPassesAndWhyNotAgree:
    """They are one function now, so they cannot drift apart — this holds them to it."""

    def test_a_reason_is_given_exactly_when_the_job_fails(self):
        from src.scoring.prefilter import passes, why_not
        for loc in ("Toronto, ON", "Austin, TX", "Remote", "Berlin, Germany"):
            job = {"title": "Developer", "location": loc, "description": "Python."}
            assert passes(job, CANADIAN_JUNIOR) is (why_not(job, CANADIAN_JUNIOR) is None)
