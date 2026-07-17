"""The restart endpoint schedules a process re-exec and replies cleanly first.

We don't actually re-exec in the test — that would kill the test runner — so os.execv
and the timer are patched. What we verify: the endpoint returns 200 with
{"restarting": True}, arms the restart on a timer (not inline), and does not fire execv
during the request itself.
"""
from unittest.mock import patch


class TestRestartEndpoint:
    def test_restart_replies_and_schedules(self, client):
        with patch("os.execv") as execv, patch("threading.Timer") as timer:
            r = client.post("/api/maint/restart")
        assert r.status_code == 200
        assert r.json()["restarting"] is True
        timer.assert_called_once()          # armed on a timer, not inline
        execv.assert_not_called()           # not fired during the request itself

    def test_schedule_restart_arms_a_timer(self):
        from src import maintenance
        with patch("threading.Timer") as timer, patch("os.execv"):
            out = maintenance.schedule_restart()
        assert out == {"restarting": True}
        timer.assert_called_once()
        # the timer's delay is short but non-zero, so the HTTP reply can flush first
        delay = timer.call_args[0][0]
        assert 0 < delay < 5
