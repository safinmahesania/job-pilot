"""Notifications: stored on send, listed newest-first, marked seen on open."""
from unittest.mock import patch

USER = "00000000-0000-0000-0000-000000000001"

class TestNotificationRecording:
    def test_send_records_even_when_telegram_is_off(self, conn, monkeypatch):
        # Telegram disabled, and not in the pytest-guard path: _record should still run.
        import src.notify as notify
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        with patch("src.notify.enabled", return_value=False), \
             patch("src.store.connect", return_value=conn):
            notify.send("hello world")
        row = conn.execute("SELECT text, seen FROM notifications").fetchone()
        assert row[0] == "hello world"
        assert row[1] == 0

    def test_only_the_last_100_are_kept(self, conn, monkeypatch):
        import src.notify as notify
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        with patch("src.notify.enabled", return_value=False), \
             patch("src.store.connect", return_value=conn):
            for i in range(110):
                notify.send(f"msg {i}")
        n = conn.execute("SELECT count(*) FROM notifications").fetchone()[0]
        assert n == 100
        # the oldest were trimmed
        newest = conn.execute("SELECT text FROM notifications ORDER BY id DESC LIMIT 1").fetchone()[0]
        assert newest == "msg 109"


class TestNotificationEndpoints:
    def _add(self, conn, text, seen=0):
        conn.execute("INSERT INTO notifications (user_id, text, seen) VALUES (?,?,?)",
                     (USER, text, bool(seen)))
        conn.commit()

    def test_list_returns_newest_first_with_unseen_count(self, client, conn):
        self._add(conn, "first", seen=1)
        self._add(conn, "second", seen=0)
        self._add(conn, "third", seen=0)
        d = client.get("/api/notifications").json()
        assert [n["text"] for n in d["notifications"]] == ["third", "second", "first"]
        assert d["unseen"] == 2

    def test_marking_seen_clears_the_unseen_count(self, client, conn):
        self._add(conn, "a", seen=0)
        self._add(conn, "b", seen=0)
        assert client.get("/api/notifications").json()["unseen"] == 2
        client.post("/api/notifications/seen")
        assert client.get("/api/notifications").json()["unseen"] == 0
