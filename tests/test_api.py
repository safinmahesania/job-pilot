"""The HTTP surface.

Mostly a smoke test — every route answers, bad input is refused rather than
swallowed. Two of these are structural, though, and worth more than they look:

`test_the_static_mount_is_last` guards a trap in FastAPI. `app.mount("/", ...)`
catches everything, so any route declared after it is unreachable — and the
failure is not an error, it's a 404 on an endpoint that plainly exists in the
source. Someone adds a handler at the bottom of the file, it never runs, and they
lose an afternoon.

`test_every_route_the_frontend_calls_exists` is the reverse: the frontend and the
API drift apart silently, because a fetch to a route that doesn't exist just
fails quietly in a `.catch()`.
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(db):
    from src import api
    return TestClient(api.app)


class TestStructure:
    def test_the_static_mount_is_last(self):
        """Anything declared after it is dead code that returns 404."""
        source = (ROOT / "src" / "api.py").read_text(encoding="utf-8")

        mount_at = source.index('app.mount("/"')
        after = source[mount_at:]

        assert "@app.get" not in after and "@app.post" not in after, (
            "a route is declared after the static mount — it will never be "
            "reachable. Move it above app.mount()."
        )

    def test_every_route_the_frontend_calls_exists(self):
        api_source = (ROOT / "src" / "api.py").read_text(encoding="utf-8")
        js = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        routes = [m.group(2) for m in
                  re.finditer(r'@app\.(get|post|delete|put)\("([^"]+)"', api_source)]
        called = set(re.findall(r"""['"`](/api/[^'"`?\s]+)""", js))

        def compatible(call: str, route: str) -> bool:
            """Could these two ever be the same URL?

            Compared segment by segment. A `${...}` on the frontend and a `{...}`
            on the backend are both wildcards — the frontend builds some paths
            dynamically (`/api/jobs/${id}/${kind}`), so a literal string compare
            would report a false missing route.
            """
            a, b = call.strip("/").split("/"), route.strip("/").split("/")
            if len(a) != len(b):
                return False
            for x, y in zip(a, b):
                if x.startswith("${") or (y.startswith("{") and y.endswith("}")):
                    continue
                if x != y:
                    return False
            return True

        missing = [c for c in called
                   if not any(compatible(c, r) for r in routes)]

        assert not missing, f"the frontend calls routes that don't exist: {missing}"


class TestReads:
    @pytest.mark.parametrize("path", [
        "/api/counts", "/api/settings", "/api/jobs", "/api/jobs?tab=saved",
        "/api/jobs?tab=unscored", "/api/sources", "/api/health", "/api/stats",
        "/api/runs", "/api/schedule", "/api/model", "/api/notify",
        "/api/llm/providers", "/api/ai-features", "/api/privacy",
        "/api/feedback", "/api/followups", "/api/import/template",
    ])
    def test_it_answers(self, client, path):
        assert client.get(path).status_code == 200

    def test_counts_carry_the_followup_badge(self, client):
        assert "followups" in client.get("/api/counts").json()


class TestWrites:
    def test_a_valid_privacy_mode_is_accepted(self, client):
        r = client.post("/api/privacy",
                        json={"mode": "local", "follow_job_links": False})
        assert r.status_code == 200

    def test_an_invalid_privacy_mode_is_refused(self, client):
        """Not silently coerced to a default. A wrong privacy mode is not a
        typo to be fixed helpfully."""
        r = client.post("/api/privacy", json={"mode": "send-everything-anywhere"})
        assert r.status_code == 400

    def test_an_invalid_status_is_refused(self, client):
        r = client.post("/api/jobs/1/status", json={"status": "bogus"})
        assert r.status_code == 400

    def test_the_threshold_is_clamped_not_rejected(self, client):
        client.post("/api/settings/threshold", json={"value": 9999})
        assert client.get("/api/settings").json()["score_threshold"] == 100

    def test_an_empty_csv_is_a_400_not_a_silent_success(self, client):
        r = client.post("/api/import/file",
                        files={"file": ("jobs.csv", b"title,company\n", "text/csv")})
        assert r.status_code == 400

    def test_binary_junk_is_a_400(self, client):
        r = client.post("/api/import/file",
                        files={"file": ("jobs.csv", b"\x00\x01\x02", "text/csv")})
        assert r.status_code == 400


class TestFollowupEndpoints:
    def _applied_job(self, client):
        from src import store
        conn = store.connect()
        conn.execute(
            "INSERT INTO jobs (dedupe_hash, source, title, company, apply_url, "
            "status, applied_on) VALUES "
            "('x','test','Dev','Shopify','https://x/1','applied','2026-01-01')"
        )
        conn.commit()
        job_id = conn.execute("SELECT id FROM jobs").fetchone()[0]
        conn.close()
        return job_id

    def test_an_old_application_shows_up_as_due(self, client):
        self._applied_job(client)
        data = client.get("/api/followups").json()
        assert data["total"] == 1

    def test_marking_it_done_clears_it(self, client):
        job_id = self._applied_job(client)

        r = client.post(f"/api/jobs/{job_id}/followup", json={"action": "done"})
        assert r.status_code == 200

        assert client.get("/api/followups").json()["total"] == 0

    def test_snoozing_clears_it(self, client):
        job_id = self._applied_job(client)

        client.post(f"/api/jobs/{job_id}/followup",
                    json={"action": "snooze", "days": 7})

        assert client.get("/api/followups").json()["total"] == 0

    def test_an_unknown_action_is_refused(self, client):
        job_id = self._applied_job(client)
        r = client.post(f"/api/jobs/{job_id}/followup", json={"action": "ignore"})
        assert r.status_code == 400

    def test_a_job_that_does_not_exist_is_a_404(self, client):
        r = client.post("/api/jobs/99999/followup", json={"action": "done"})
        assert r.status_code == 404


class TestMissingThings:
    def test_generating_for_a_nonexistent_job_is_a_404(self, client):
        assert client.post("/api/jobs/99999/cover-letter").status_code == 404

    def test_a_material_that_was_never_saved_is_a_404(self, client):
        r = client.get("/api/jobs/99999/materials/cover/file")
        assert r.status_code == 404

    def test_an_unknown_tab_returns_an_empty_list_not_an_error(self, client):
        r = client.get("/api/jobs?tab=nonsense")
        assert r.status_code == 200
        assert r.json() == []
