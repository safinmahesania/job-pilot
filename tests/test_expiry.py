"""Dismissing jobs whose application deadline has passed.

A posting that closed last week sits in the feed looking open. The deadline is read from
the field the adapter stored, or from a date written into the description — "apply by
March 15" — and a past one means the job is dismissed. Ambiguous dates are left alone: a
wrong guess dismisses a job that is still open, which is worse than missing one that has
closed.
"""
from datetime import date

from src import expiry


class TestReadingTheDeadline:
    def test_a_stored_iso_field_is_read(self):
        assert expiry.deadline_for({"deadline": "2025-03-15"}) == date(2025, 3, 15)

    def test_apply_by_month_day_year_in_the_text(self):
        job = {"description": "Great role. Apply by March 15, 2025. Remote OK."}
        assert expiry.deadline_for(job) == date(2025, 3, 15)

    def test_closing_date_iso_in_the_text(self):
        job = {"description": "Closing date: 2025-12-31. Send your CV."}
        assert expiry.deadline_for(job) == date(2025, 12, 31)

    def test_day_month_year_in_the_text(self):
        job = {"description": "Deadline 20 May 2025 for all applications."}
        assert expiry.deadline_for(job) == date(2025, 5, 20)

    def test_the_stored_field_wins_over_the_text(self):
        job = {"deadline": "2025-01-01", "description": "apply by 31 December 2025"}
        assert expiry.deadline_for(job) == date(2025, 1, 1)


class TestWhatIsNotADeadline:
    def test_a_founding_year_is_not_a_deadline(self):
        assert expiry.deadline_for({"description": "Founded in 2019, we grew fast."}) is None

    def test_a_posted_date_with_no_cue_is_not_a_deadline(self):
        assert expiry.deadline_for({"description": "Posted on 2025-05-01."}) is None

    def test_a_salary_figure_is_not_a_deadline(self):
        assert expiry.deadline_for({"description": "Salary $120,000 per year."}) is None

    def test_no_date_at_all_is_none(self):
        assert expiry.deadline_for({"description": "A remote Python role."}) is None


class TestHasExpired:
    def test_a_past_deadline_has_expired(self):
        job = {"deadline": "2025-01-01"}
        assert expiry.has_expired(job, today=date(2025, 6, 1)) is True

    def test_a_future_deadline_has_not(self):
        job = {"deadline": "2025-12-31"}
        assert expiry.has_expired(job, today=date(2025, 6, 1)) is False

    def test_no_deadline_is_not_expired(self):
        """No known deadline is not the same as a closed one — most jobs have none."""
        assert expiry.has_expired({"description": "no dates"}) is False


class TestTheSweepEndpoint:
    def _add(self, conn, jid, status, deadline=None, desc="a role"):
        conn.execute(
            "INSERT INTO jobs (id, dedupe_hash, title, company, status, deadline, "
            "description) VALUES (?, ?, 'Dev', 'X', ?, ?, ?)",
            (jid, f"h{jid}", status, deadline, desc))
        conn.commit()

    def test_it_dismisses_a_surfaced_job_past_its_deadline(self, client, conn):
        self._add(conn, 1, "surfaced", deadline="2000-01-01")
        body = client.post("/api/jobs/sweep-expired").json()
        assert body["dismissed"] == 1
        status = conn.execute("SELECT status FROM jobs WHERE id=1").fetchone()[0]
        assert status == "dismissed"

    def test_it_leaves_a_job_with_a_future_deadline(self, client, conn):
        self._add(conn, 2, "surfaced", deadline="2099-12-31")
        body = client.post("/api/jobs/sweep-expired").json()
        assert body["dismissed"] == 0

    def test_it_does_not_touch_a_saved_job(self, client, conn):
        """Something already saved is the user's call, not a deadline's."""
        self._add(conn, 3, "saved", deadline="2000-01-01")
        client.post("/api/jobs/sweep-expired")
        assert conn.execute("SELECT status FROM jobs WHERE id=3").fetchone()[0] == "saved"

    def test_it_reads_a_deadline_out_of_the_description(self, client, conn):
        self._add(conn, 4, "surfaced", desc="Apply by 1 January 2000. Great team.")
        body = client.post("/api/jobs/sweep-expired").json()
        assert body["dismissed"] == 1
