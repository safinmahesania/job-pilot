"""Tests for POST /api/sources/test — fetch one source without a full run.

The endpoint must: fetch an inline or indexed source, cap the preview, report a bad ats
and a fetch failure as clean results (not 500s), and never touch the database.
"""
from unittest.mock import MagicMock, patch


def _greenhouse_page(n):
    resp = MagicMock()
    resp.json = lambda: {"jobs": [
        {"title": f"Job {i}", "location": {"name": "Toronto"},
         "absolute_url": f"https://x.com/{i}", "content": "<p>desc</p>",
         "updated_at": "2026-07-01"} for i in range(n)
    ]}
    resp.raise_for_status = lambda: None
    return resp


class TestSourceTestEndpoint:
    def test_inline_source_fetches_and_previews(self, client):
        with patch("httpx.get", return_value=_greenhouse_page(3)):
            r = client.post("/api/sources/test", json={
                "source": {"name": "TestCo", "ats": "greenhouse", "identifier": "testco"}})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["count"] == 3
        assert d["jobs"][0]["title"] == "Job 0"
        assert "elapsed_ms" in d

    def test_preview_is_capped_by_limit(self, client):
        with patch("httpx.get", return_value=_greenhouse_page(50)):
            r = client.post("/api/sources/test", json={
                "source": {"name": "Big", "ats": "greenhouse", "identifier": "big"},
                "limit": 5})
        d = r.json()
        assert d["count"] == 50          # true total reported
        assert d["shown"] == 5           # but preview capped
        assert len(d["jobs"]) == 5

    def test_bad_ats_is_a_clean_result_not_500(self, client):
        r = client.post("/api/sources/test", json={
            "source": {"name": "X", "ats": "nonexistent"}})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is False
        assert d["stage"] == "adapter"
        assert d["count"] == 0

    def test_fetch_failure_is_a_clean_result_not_500(self, client):
        with patch("httpx.get", side_effect=Exception("connection reset")):
            r = client.post("/api/sources/test", json={
                "source": {"name": "Flaky", "ats": "greenhouse", "identifier": "f"}})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is False
        assert d["stage"] == "fetch"
        assert "connection reset" in d["error"]

    def test_index_out_of_range_is_404(self, client):
        r = client.post("/api/sources/test", json={"index": 99999})
        assert r.status_code == 404

    def test_neither_index_nor_source_is_400(self, client):
        r = client.post("/api/sources/test", json={})
        assert r.status_code == 400

    def test_nothing_is_saved_to_the_database(self, client, db):
        import sqlite3
        with patch("httpx.get", return_value=_greenhouse_page(5)):
            client.post("/api/sources/test", json={
                "source": {"name": "Dry", "ats": "greenhouse", "identifier": "dry"}})
        # the preview must not create job rows
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        conn.close()
        assert count == 0
