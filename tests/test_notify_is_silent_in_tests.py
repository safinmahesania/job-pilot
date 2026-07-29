"""No test may message a real phone.

A test that calls into the scheduler's crash branch would otherwise reach notify.send,
which reads the real Telegram token and posts to it — which is how a test's placeholder
error, "the pipeline fell over", arrived as a notification on the developer's phone.
Individual tests stub notify, but this is the net for the one that forgets.
"""
import os

from src import notify


class TestSendIsSuppressedUnderPytest:
    def test_send_returns_false_while_a_test_is_running(self, monkeypatch):
        # Make it look fully configured, so the only thing that can stop it is the
        # pytest guard.
        monkeypatch.setattr(notify, "enabled", lambda: True)
        monkeypatch.setattr(notify, "_token", lambda: "fake-token")
        monkeypatch.setattr(notify, "_chat_id", lambda: "123")

        called = {"posted": False}

        def _boom(*a, **k):
            called["posted"] = True
            raise AssertionError("a real Telegram request was attempted from a test")

        monkeypatch.setattr(notify.httpx, "post", _boom)

        assert notify.send("anything") is False
        assert called["posted"] is False

    def test_the_guard_is_the_pytest_env_var(self):
        """Sanity: the variable the guard relies on is actually set in this process."""
        assert os.environ.get("PYTEST_CURRENT_TEST")
