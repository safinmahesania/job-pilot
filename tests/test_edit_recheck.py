"""An edited job goes back through the filters, not just the scorer.

A job fetched with half a description was judged on half a description. Once the real
posting is pasted in, things that were unknowable become knowable — that it is in
Austin, that it is a staff role — and a job that looked fine on its title turns out
not to be one to keep. A fetch run drops such a job; an edit used to leave it sitting
in the feed with a fresh score and a constraint it plainly fails.
"""
from unittest.mock import patch

USER = "00000000-0000-0000-0000-000000000001"



CANADIAN_JUNIOR = {
    "constraints": {"locations": ["remote", "toronto", "ontario", "canada"]},
    "search": {"role_levels": ["junior", "intern"]},
    "skills": {"expert": ["Python"]},
}


def _job(conn, **cols):
    status = cols.pop("status", "surfaced")
    base = {"dedupe_hash": "r1", "title": "Developer", "company": "X",
            "location": "Toronto, ON", "description": "Python work."}
    base.update(cols)
    keys = ", ".join(base)
    marks = ", ".join("?" * len(base))
    jid = conn.execute(
        f"INSERT INTO jobs ({keys}) VALUES ({marks}) RETURNING id",
        list(base.values())).fetchone()[0]
    conn.execute("INSERT INTO user_jobs (user_id, job_id, status) VALUES (?,?,?)",
                 (USER, jid, status))
    conn.commit()
    return jid


class TestRecheck:
    def test_a_job_that_still_fits_is_left_alone(self, client, conn):
        jid = _job(conn)
        with patch("src.deps._user_profile", return_value=CANADIAN_JUNIOR):
            body = client.post(f"/api/jobs/{jid}/recheck").json()
        assert body["verdict"] == "ok"
        status = conn.execute("SELECT status FROM user_jobs WHERE job_id=?", (jid,)).fetchone()[0]
        assert status == "surfaced"

    def test_a_job_outside_your_locations_is_dismissed(self, client, conn):
        jid = _job(conn, dedupe_hash="r2", location="Austin, TX")
        with patch("src.deps._user_profile", return_value=CANADIAN_JUNIOR):
            body = client.post(f"/api/jobs/{jid}/recheck").json()
        assert body["verdict"] == "dismissed"
        assert "Austin" in body["reason"]          # says which rule, and with what value
        status = conn.execute("SELECT status FROM user_jobs WHERE job_id=?", (jid,)).fetchone()[0]
        assert status == "dismissed"

    def test_the_reason_is_in_words_not_a_code(self, client, conn):
        jid = _job(conn, dedupe_hash="r3", location="Berlin, Germany")
        with patch("src.deps._user_profile", return_value=CANADIAN_JUNIOR):
            reason = client.post(f"/api/jobs/{jid}/recheck").json()["reason"]
        assert "location" in reason and len(reason.split()) > 4

    def test_no_profile_means_unchecked_not_passed(self, client, conn):
        """Claiming a job passed filters that were never applied would be a lie."""
        jid = _job(conn, dedupe_hash="r4")
        with patch("src.deps._user_profile", return_value={}):
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

    def test_editing_a_scoring_field_never_inline_rescores(self, client, conn):
        """Re-scoring is per-user and being reworked, so an edit only flags
        needs_reprocess now — it never scores inline, with or without defer."""
        jid = _job(conn, dedupe_hash="r6")
        with patch("src.routes.jobs._rescore_one") as rs:
            body = client.patch(f"/api/jobs/{jid}",
                                json={"description": "A longer, real description."}).json()
        rs.assert_not_called()
        assert body["rescored"] is None
        assert body["needs_reprocess"] is True

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
