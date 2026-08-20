"""Core multi-user regression suite for the Postgres migration.

Focused, high-value checks that the shared-pool + per-user model holds: the schema
shape, that jobs is a shared pool while judgement lives in user_jobs, that one user
can't see another's rows, the get-new scoring flow, and the per-user read surfaces
(counts, followups, materials). This is the safety net that guards the migration; it
is not a port of the old single-user unit tests.

Run:  TEST_DATABASE_URL=postgresql://... pytest tests/test_multiuser_core.py -v
"""
import pytest

USER = "00000000-0000-0000-0000-000000000001"
USER2 = "00000000-0000-0000-0000-000000000002"

DESC = ("A sufficiently long job description that clears the extractor and scorer "
        "minimum length so this counts as a real, scorable posting for the tests.")


def _add_pool_job(conn, dedupe, title="Backend Dev", company="Acme",
                  location="Toronto, Canada", description=DESC):
    """Insert a job into the shared pool, return its id."""
    conn.execute(
        "INSERT INTO jobs (dedupe_hash, title, company, location, description) "
        "VALUES (?,?,?,?,?)", (dedupe, title, company, location, description))
    conn.commit()
    return conn.execute("SELECT id FROM jobs WHERE dedupe_hash=?", (dedupe,)).fetchone()[0]


def _surface(conn, user_id, job_id, status="surfaced", score=None):
    conn.execute(
        "INSERT INTO user_jobs (user_id, job_id, status, score, served_at) "
        "VALUES (?,?,?,?, now())", (user_id, job_id, status, score))
    conn.commit()


# ── schema shape ─────────────────────────────────────────────────────────────

class TestSchema:
    def test_jobs_is_pool_only(self, db):
        cols = {r[0] for r in db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='jobs'").fetchall()}
        assert "status" not in cols and "score" not in cols
        assert {"dedupe_hash", "title", "extracted_at", "quality_flags"} <= cols

    def test_user_jobs_carries_the_judgement(self, db):
        cols = {r[0] for r in db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='user_jobs'").fetchall()}
        assert {"user_id", "job_id", "status", "score", "applied_on"} <= cols

    def test_per_user_tables_have_owner(self, db):
        for table in ("materials", "application_answers", "notifications"):
            cols = {r[0] for r in db.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=?", (table,)).fetchall()}
            assert "user_id" in cols, table


# ── shared pool + per-user isolation ─────────────────────────────────────────

class TestPoolAndIsolation:
    def test_one_pool_job_two_users(self, db):
        jid = _add_pool_job(db, "shared1")
        _surface(db, USER, jid, "applied", 90)
        _surface(db, USER2, jid, "dismissed", 40)
        # One pool row, two independent judgements.
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        u1 = db.execute("SELECT status, score FROM user_jobs WHERE user_id=? AND job_id=?",
                        (USER, jid)).fetchone()
        u2 = db.execute("SELECT status, score FROM user_jobs WHERE user_id=? AND job_id=?",
                        (USER2, jid)).fetchone()
        assert (u1[0], u1[1]) == ("applied", 90)
        assert (u2[0], u2[1]) == ("dismissed", 40)

    def test_counts_are_per_user(self, db):
        from src.routes import jobs as J
        jid = _add_pool_job(db, "c1")
        _surface(db, USER, jid, "applied", 80)
        assert J.counts(user_id=USER, conn=db)["applied"] == 1
        assert J.counts(user_id=USER2, conn=db)["applied"] == 0

    def test_materials_are_per_user(self, db):
        from src import materials
        jid = _add_pool_job(db, "m1")
        materials.save(USER, jid, "cover", "user one letter", "test")
        materials.save(USER2, jid, "cover", "user two letter", "test")
        assert materials.get(USER, jid, "cover")["content"] == "user one letter"
        assert materials.get(USER2, jid, "cover")["content"] == "user two letter"
        # deleting one leaves the other
        assert materials.delete(USER, jid, "cover") is True
        assert materials.get(USER, jid, "cover") is None
        assert materials.get(USER2, jid, "cover") is not None


# ── get-new scoring flow ─────────────────────────────────────────────────────

class TestGetNew:
    def _mock_scorer(self, monkeypatch, score=82):
        import src.scoring.rerank as rerank

        class R:
            def __init__(s, v):
                s.overall = v
                s.skills_score = v
                s.seniority_score = v
                s.domain_score = v
                s.rationale = "mock"
        monkeypatch.setattr(rerank, "score_job", lambda job, prof, cal="": R(score))

    def test_prefilter_dismisses_out_of_region(self, db, monkeypatch):
        self._mock_scorer(monkeypatch)
        from src.routes import jobs as J
        _add_pool_job(db, "ca", location="Toronto, Canada")
        _add_pool_job(db, "us", location="Austin, USA")
        out = J.get_new_jobs(user_id=USER, conn=db)
        assert out["scored"] == 1          # Canada passes
        assert out["filtered"] == 1        # USA filtered
        # the USA job is recorded as dismissed so it's not re-evaluated next time
        again = J.get_new_jobs(user_id=USER, conn=db)
        assert again["scored"] == 0 and again["filtered"] == 0

    def test_scores_land_on_user_jobs(self, db, monkeypatch):
        self._mock_scorer(monkeypatch, score=77)
        from src.routes import jobs as J
        jid = _add_pool_job(db, "s1", location="Remote")
        J.get_new_jobs(user_id=USER, conn=db)
        row = db.execute("SELECT status, score FROM user_jobs WHERE user_id=? AND job_id=?",
                         (USER, jid)).fetchone()
        assert row[0] == "surfaced" and row[1] == 77

    def test_no_profile_short_circuits(self, db, monkeypatch):
        from src.routes import jobs as J
        db.execute("DELETE FROM user_profiles WHERE user_id=?", (USER2,))
        db.commit()
        out = J.get_new_jobs(user_id=USER2, conn=db)
        assert out["needs_profile"] is True


# ── on-demand scoring + cleanup (admin route, per-user) ──────────────────────

class TestScoringActions:
    def test_maint_cleanup_dismisses_below_threshold(self, db):
        from src.routes import admin as A
        low = _add_pool_job(db, "low")
        high = _add_pool_job(db, "high")
        _surface(db, USER, low, "surfaced", 40)
        _surface(db, USER, high, "surfaced", 85)
        A.maint_cleanup(user_id=USER, conn=db)     # threshold defaults to 60
        st = dict(db.execute(
            "SELECT job_id, status FROM user_jobs WHERE user_id=?", (USER,)).fetchall())
        assert st[low] == "dismissed"
        assert st[high] == "surfaced"


# ── per-user follow-ups ──────────────────────────────────────────────────────

class TestFollowups:
    def test_applied_long_ago_is_due(self, db):
        from src import followups
        jid = _add_pool_job(db, "f1")
        db.execute(
            "INSERT INTO user_jobs (user_id, job_id, status, applied_on, served_at) "
            "VALUES (?,?, 'applied', now() - interval '10 days', now())", (USER, jid))
        db.commit()
        items = followups.due(db, USER)
        assert len(items) == 1 and items[0]["stage"] == "first"
        # isolation: the other user has nothing due
        assert followups.summary(db, USER2)["total"] == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
