"""Follow-up reminders.

The rules are about restraint as much as reminding. A tool that nags you about
every application every day gets muted within a week, and then it is worse than
useless — you have a reminder system you have learned to ignore.

So: nothing before the first window has passed. Nothing at all for a job that has
moved on to interview or rejected, where the conversation is live or over. Nothing
for a job you have snoozed. And past the stale mark it stops asking you to chase
and tells you plainly to close it out — the honest read on a month of silence.
"""
from datetime import date, timedelta

import pytest

from src import followups
from src.paths import (
    FOLLOWUP_FIRST_DAYS, FOLLOWUP_SECOND_DAYS, FOLLOWUP_STALE_DAYS,
)

USER = "00000000-0000-0000-0000-000000000001"


def days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def applied(conn, company, days, status="applied", followed_up=None, snooze=None):
    """Seed a pool job plus this user's judgement (status/dates live in user_jobs)."""
    conn.execute(
        "INSERT INTO jobs (dedupe_hash, source, title, company, apply_url) "
        "VALUES (?,?,?,?,?)",
        (company, "test", "Backend Dev", company, f"https://x/{company}"))
    jid = conn.execute("SELECT id FROM jobs WHERE dedupe_hash=?", (company,)).fetchone()[0]
    conn.execute(
        "INSERT INTO user_jobs (user_id, job_id, status, applied_on, "
        "followed_up_on, followup_snooze, served_at) VALUES (?,?,?,?,?,?, now())",
        (USER, jid, status, days_ago(days), followed_up, snooze))
    conn.commit()
    return jid


class TestWhenAFollowUpIsDue:
    def test_not_before_the_first_window(self, conn):
        applied(conn, "Shopify", FOLLOWUP_FIRST_DAYS - 1)
        assert followups.due(conn, USER) == []

    def test_due_once_the_window_passes(self, conn):
        applied(conn, "Shopify", FOLLOWUP_FIRST_DAYS)

        items = followups.due(conn, USER)

        assert len(items) == 1
        assert items[0]["stage"] == "first"
        assert items[0]["company"] == "Shopify"

    def test_a_second_nudge_after_the_first_goes_unanswered(self, conn):
        applied(conn, "Shopify", FOLLOWUP_FIRST_DAYS + FOLLOWUP_SECOND_DAYS,
                followed_up=days_ago(FOLLOWUP_SECOND_DAYS))

        items = followups.due(conn, USER)

        assert items[0]["stage"] == "second"

    def test_no_second_nudge_before_its_window(self, conn):
        applied(conn, "Shopify", FOLLOWUP_FIRST_DAYS + 1,
                followed_up=days_ago(1))
        assert followups.due(conn, USER) == []

    def test_past_the_stale_mark_it_stops_chasing(self, conn):
        """A month of silence is an answer. Saying so is more useful than another
        reminder to chase."""
        applied(conn, "Shopify", FOLLOWUP_STALE_DAYS + 5)

        items = followups.due(conn, USER)

        assert items[0]["stage"] == "stale"
        assert "close it out" in items[0]["reason"]


class TestWhatItLeavesAlone:
    @pytest.mark.parametrize("status", ["interview", "offer", "rejected",
                                        "saved", "surfaced", "dismissed"])
    def test_only_applied_jobs_are_chased(self, conn, status):
        """An interview is a live conversation; a rejection is over. Neither wants
        a reminder."""
        applied(conn, "Shopify", 30, status=status)
        assert followups.due(conn, USER) == []

    def test_a_snoozed_job_stays_quiet(self, conn):
        applied(conn, "Shopify", FOLLOWUP_FIRST_DAYS + 5,
                snooze=(date.today() + timedelta(days=3)).isoformat())
        assert followups.due(conn, USER) == []

    def test_but_it_comes_back_when_the_snooze_expires(self, conn):
        applied(conn, "Shopify", FOLLOWUP_FIRST_DAYS + 5,
                snooze=days_ago(1))
        assert len(followups.due(conn, USER)) == 1

    def test_a_job_with_no_applied_date_is_skipped(self, conn):
        conn.execute("INSERT INTO jobs (dedupe_hash, source, title, company) "
                     "VALUES ('x','test','Dev','Shopify')")
        jid = conn.execute("SELECT id FROM jobs WHERE dedupe_hash='x'").fetchone()[0]
        conn.execute("INSERT INTO user_jobs (user_id, job_id, status, served_at) "
                     "VALUES (?,?, 'applied', now())", (USER, jid))
        conn.commit()
        assert followups.due(conn, USER) == []


class TestActions:
    def test_marking_it_done_restarts_the_clock(self, conn):
        applied(conn, "Shopify", FOLLOWUP_FIRST_DAYS)
        job_id = followups.due(conn, USER)[0]["id"]

        assert followups.mark_followed_up(conn, USER, job_id)

        assert followups.due(conn, USER) == []          # not due again for a while
        row = conn.execute("SELECT followed_up_on FROM user_jobs "
                           "WHERE user_id=? AND job_id=?", (USER, job_id)).fetchone()
        assert str(row[0])[:10] == date.today().isoformat()

    def test_snoozing_pushes_it_out(self, conn):
        applied(conn, "Shopify", FOLLOWUP_FIRST_DAYS)
        job_id = followups.due(conn, USER)[0]["id"]

        assert followups.snooze(conn, USER, job_id, days=7)

        assert followups.due(conn, USER) == []

    def test_marking_a_job_that_is_not_applied_does_nothing(self, conn):
        applied(conn, "Shopify", 10, status="saved")
        job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]

        assert followups.mark_followed_up(conn, USER, job_id) is False


class TestOrderingAndSummary:
    def test_the_most_overdue_comes_first(self, conn):
        applied(conn, "Recent", FOLLOWUP_FIRST_DAYS)
        applied(conn, "Ancient", FOLLOWUP_FIRST_DAYS + 20)

        items = followups.due(conn, USER)

        assert items[0]["company"] == "Ancient"

    def test_summary_counts_each_stage(self, conn):
        applied(conn, "First", FOLLOWUP_FIRST_DAYS)
        applied(conn, "Second", FOLLOWUP_FIRST_DAYS + FOLLOWUP_SECOND_DAYS,
                followed_up=days_ago(FOLLOWUP_SECOND_DAYS))
        applied(conn, "Stale", FOLLOWUP_STALE_DAYS + 5)

        summary = followups.summary(conn, USER)

        assert summary == {"total": 3, "first": 1, "second": 1, "stale": 1}


class TestNotification:
    def test_nothing_due_means_no_message(self, conn):
        assert followups.notification(conn, USER) is None

    def test_the_message_names_the_jobs(self, conn):
        applied(conn, "Shopify", FOLLOWUP_FIRST_DAYS)

        message = followups.notification(conn, USER)

        assert "Shopify" in message
        assert "1 follow-up due" in message

    def test_a_long_list_is_truncated(self, conn):
        """A reminder that reads like a report gets ignored."""
        for i in range(12):
            applied(conn, f"Co{i}", FOLLOWUP_FIRST_DAYS + i)

        message = followups.notification(conn, USER)

        assert "and 4 more" in message
        assert len(message.splitlines()) <= 10
