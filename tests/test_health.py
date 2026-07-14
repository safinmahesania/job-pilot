"""Source health, and the failure nobody notices.

The test that matters here is `test_a_board_that_returns_nothing_is_caught`. A
scraper almost never fails with an exception — it fails by returning HTTP 200 and
an empty list, because the API grew a parameter or the company left the ATS. The
run succeeds. The dashboard is green. You just stop getting jobs from that source,
forever, and nothing ever tells you.

Everything else in this file exists to make sure the detection doesn't become
noise: one quiet day is not a broken board, a recovery clears the record, and a
board that has been dead for a month is not news every four hours.
"""
import pytest

from src import health, notify, store
from src.paths import HEALTH_ZERO_STREAK, HEALTH_ERROR_STREAK


def record(conn, name, fetched=10, status="ok", error=None, kept=2,
           when="2026-07-13 09:00"):
    store.save_source_health(
        conn, name, "greenhouse",
        {"fetched": fetched, "kept": kept, "status": status, "error": error},
        when,
    )


def verdict(conn, name):
    return next(b["verdict"] for b in health.assess(conn) if b["name"] == name)


class TestSilentDeath:
    def test_a_board_that_returns_nothing_is_caught(self, conn):
        """The whole reason this module exists.

        Status 'ok' every time. No exception. No error message. Nothing else in
        the app would ever have told you this board stopped working.
        """
        record(conn, "Shopify", fetched=20)              # it used to work

        for _ in range(HEALTH_ZERO_STREAK):
            record(conn, "Shopify", fetched=0)           # 200 OK, zero jobs

        assert verdict(conn, "Shopify") == "silent"

    def test_one_quiet_run_is_not_a_broken_board(self, conn):
        """A company may simply have no openings today. Don't cry wolf."""
        record(conn, "Shopify", fetched=20)
        record(conn, "Shopify", fetched=0)

        assert verdict(conn, "Shopify") == "wobbling"

    def test_a_board_that_never_worked_says_so(self, conn):
        """Usually a bad slug — or an adapter that is still a stub."""
        for _ in range(HEALTH_ZERO_STREAK):
            record(conn, "SomeCustomCo", fetched=0)

        assert verdict(conn, "SomeCustomCo") == "never_worked"

    def test_the_detail_says_when_it_last_worked(self, conn):
        record(conn, "Shopify", fetched=20, when="2026-06-01 09:00")
        for _ in range(HEALTH_ZERO_STREAK):
            record(conn, "Shopify", fetched=0)

        board = next(b for b in health.assess(conn) if b["name"] == "Shopify")
        assert "2026-06-01" in board["detail"]


class TestLoudFailure:
    def test_repeated_errors_are_reported(self, conn):
        for _ in range(HEALTH_ERROR_STREAK):
            record(conn, "DeadCo", fetched=0, status="error", error="HTTP 404")

        assert verdict(conn, "DeadCo") == "erroring"

    def test_one_error_is_not_conclusive(self, conn):
        record(conn, "DeadCo", fetched=10)
        record(conn, "DeadCo", fetched=0, status="error", error="timeout")

        assert verdict(conn, "DeadCo") == "wobbling"


class TestRecovery:
    def test_a_working_run_wipes_the_slate(self, conn):
        for _ in range(HEALTH_ZERO_STREAK):
            record(conn, "Shopify", fetched=0)
        assert verdict(conn, "Shopify") in health.BROKEN

        record(conn, "Shopify", fetched=15)             # it's back

        assert verdict(conn, "Shopify") == "ok"

    def test_a_board_that_breaks_again_tells_you_again(self, conn):
        """Recovery clears the alert flag, so the second breakage is news too."""
        for _ in range(HEALTH_ZERO_STREAK):
            record(conn, "Shopify", fetched=0)
        store.mark_health_alerted(conn, ["Shopify"])
        assert health.new_breakages(conn) == []

        record(conn, "Shopify", fetched=15)             # recovered
        for _ in range(HEALTH_ZERO_STREAK):             # broke again
            record(conn, "Shopify", fetched=0)

        assert [b["name"] for b in health.new_breakages(conn)] == ["Shopify"]


class TestAlertsAreNotNoise:
    def test_a_broken_board_is_reported_once(self, conn):
        """It was news the first time. It is not news every four hours."""
        for _ in range(HEALTH_ZERO_STREAK):
            record(conn, "Shopify", fetched=0)

        first = health.new_breakages(conn)
        assert len(first) == 1

        store.mark_health_alerted(conn, ["Shopify"])
        record(conn, "Shopify", fetched=0)              # still dead

        assert health.new_breakages(conn) == []

    def test_healthy_boards_are_never_alerted_on(self, conn):
        record(conn, "Shopify", fetched=20)
        assert health.new_breakages(conn) == []


class TestSummary:
    def test_it_counts_each_kind(self, conn):
        record(conn, "Good", fetched=20)

        record(conn, "Silent", fetched=20)
        for _ in range(HEALTH_ZERO_STREAK):
            record(conn, "Silent", fetched=0)

        for _ in range(HEALTH_ERROR_STREAK):
            record(conn, "Broken", fetched=0, status="error", error="404")

        s = health.summary(conn)

        assert s["ok"] == 1
        assert s["silent"] == 1
        assert s["erroring"] == 1
        assert s["broken"] == 2


class TestMessages:
    def test_the_alert_leads_with_the_silent_ones(self, conn):
        """An erroring board already shows red. A silent one shows green — which
        is why it goes first."""
        record(conn, "Silent", fetched=20)
        for _ in range(HEALTH_ZERO_STREAK):
            record(conn, "Silent", fetched=0)
        for _ in range(HEALTH_ERROR_STREAK):
            record(conn, "Loud", fetched=0, status="error", error="404")

        message = notify.board_alert(health.broken(conn))

        assert message.index("Silent") < message.index("Loud")
        assert "reporting success" in message

    def test_the_digest_leads_with_what_you_owe(self, conn):
        """Follow-ups are not optional. The numbers are."""
        message = notify.weekly_digest({
            "followups_due": 3, "unreviewed": 12, "new_jobs": 40,
            "applied": 2, "saved": 5, "dismissed": 20, "runs": 42,
            "broken_boards": 1,
        })

        assert message.index("follow-up") < message.index("Last 7 days")

    def test_the_digest_says_something_when_you_applied_to_nothing(self, conn):
        message = notify.weekly_digest({
            "followups_due": 0, "unreviewed": 30, "new_jobs": 60,
            "applied": 0, "saved": 4, "dismissed": 10, "runs": 42,
            "broken_boards": 0,
        })

        assert "the applications are" in message


class TestRunSummaryArithmetic:
    def test_processed_is_right_when_nothing_is_new(self, conn):
        """The old expression used `and`/`or` and reported the wrong number
        whenever a run turned up nothing new — which is most runs."""
        stats = {"fetched": 100, "seen": 100, "dropped": 0,
                 "trashed": 0, "kept": 0, "errors": 0}

        message = notify.run_summary(stats, 12.0, "qwen2.5:14b", [])

        assert "0 processed" in message
