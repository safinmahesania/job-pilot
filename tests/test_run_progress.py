"""A run says how far along it is, not just that it is happening.

A full pass can take twenty minutes. "Running" on its own leaves you guessing whether
to wait or go away, so the pipeline publishes its position and the UI works the
remaining time out from this run's own rate.
"""
from src import run


class TestProgressIsPublished:
    def setup_method(self):
        run.reset_progress()

    def test_it_starts_inactive(self):
        assert run.PROGRESS["active"] is False
        assert run.PROGRESS["total"] == 0

    def test_reset_clears_everything(self):
        run._progress(active=True, phase="Scoring", source="Adzuna",
                      done=40, total=300, started=123.0)
        run.reset_progress()
        assert run.PROGRESS["active"] is False
        assert run.PROGRESS["done"] == 0
        assert run.PROGRESS["source"] == ""

    def test_the_status_endpoint_carries_it(self, client):
        run._progress(active=True, phase="Scoring", source="Adzuna", done=7, total=99)
        body = client.get("/api/run/status").json()
        assert body["progress"]["done"] == 7
        assert body["progress"]["total"] == 99
        assert body["progress"]["source"] == "Adzuna"

    def test_the_status_endpoint_names_the_worker(self, client):
        """Which model is doing it — a local Ollama pass and a hosted one feel very
        different, and the wait is worth knowing about."""
        body = client.get("/api/run/status").json()
        assert "model" in body
        assert "active" in body["model"]


class TestProgressIsClearedWhenARunEnds:
    def test_a_crashed_run_does_not_leave_a_stale_bar(self, monkeypatch):
        """A panel frozen at "40 of 300" forever is worse than no panel."""
        from src import scheduler

        run._progress(active=True, phase="Scoring", done=40, total=300)

        def _boom(only=None):
            raise RuntimeError("the pipeline fell over")

        monkeypatch.setattr("src.run.run", _boom)
        scheduler._run_once()

        assert run.PROGRESS["active"] is False
        assert run.PROGRESS["done"] == 0
