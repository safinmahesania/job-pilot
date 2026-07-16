"""Tests for the selective run — fetch only chosen sources, active flag untouched.

A full run fetches every active source. A selective run (only=[names]) fetches exactly
those sources, matched by name, even if they're inactive — so you can pull from one board
on demand without changing what the scheduled run does.
"""
from unittest.mock import MagicMock, patch

import pytest

import src.run as run_mod


@pytest.fixture
def three_sources():
    return [
        {"name": "Alpha", "ats": "greenhouse", "identifier": "a", "active": True},
        {"name": "Beta", "ats": "lever", "identifier": "b", "active": True},
        {"name": "Gamma", "ats": "jsearch", "queries": ["dev"], "active": False},
    ]


def _run_capturing(only, companies):
    """Run the pipeline with fetch_all stubbed to record which sources it fetched."""
    fetched = []

    def fake_fetch_all(comps, respect_active=True):
        chosen = [c for c in comps if c.get("active")] if respect_active else comps
        fetched.extend(c["name"] for c in chosen)
        return []

    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = None
    conn.get_setting = lambda *a, **k: None

    with patch.object(run_mod, "load_companies", return_value=companies), \
         patch.object(run_mod, "load_profile", return_value={"skills": ["Python"]}), \
         patch.object(run_mod, "fetch_all", side_effect=fake_fetch_all), \
         patch.object(run_mod, "load_env"), \
         patch("src.store.connect", return_value=conn), \
         patch("src.store.get_setting", return_value=None):
        try:
            run_mod.run(only=only)
        except Exception:
            pass  # we only care about what fetch_all was asked to fetch
    return fetched


class TestSelectiveRun:
    def test_full_run_fetches_only_active(self, three_sources):
        fetched = _run_capturing(None, three_sources)
        assert set(fetched) == {"Alpha", "Beta"}      # Gamma is inactive
        assert "Gamma" not in fetched

    def test_selective_run_fetches_only_named(self, three_sources):
        fetched = _run_capturing(["Alpha"], three_sources)
        assert fetched == ["Alpha"]

    def test_selective_run_fetches_inactive_source(self, three_sources):
        # Gamma is inactive, but a selective run picks it up when named.
        fetched = _run_capturing(["Gamma"], three_sources)
        assert fetched == ["Gamma"]

    def test_selective_run_name_match_is_case_insensitive(self, three_sources):
        fetched = _run_capturing(["alpha"], three_sources)
        assert fetched == ["Alpha"]

    def test_selective_run_multiple_sources(self, three_sources):
        fetched = _run_capturing(["Alpha", "Gamma"], three_sources)
        assert set(fetched) == {"Alpha", "Gamma"}


class TestFetchAllRespectActive:
    def test_respect_active_true_filters(self):
        comps = [{"name": "A", "active": True}, {"name": "B", "active": False}]
        with patch.object(run_mod, "_fetch_one",
                          side_effect=lambda c: (c, [], {"fetched": 0, "kept": 0})):
            results = run_mod.fetch_all(comps, respect_active=True)
        assert {r[0]["name"] for r in results} == {"A"}

    def test_respect_active_false_keeps_all(self):
        comps = [{"name": "A", "active": True}, {"name": "B", "active": False}]
        with patch.object(run_mod, "_fetch_one",
                          side_effect=lambda c: (c, [], {"fetched": 0, "kept": 0})):
            results = run_mod.fetch_all(comps, respect_active=False)
        assert {r[0]["name"] for r in results} == {"A", "B"}


class TestRunEndpoint:
    def test_run_endpoint_accepts_selection(self, client):
        with patch("src.scheduler.trigger_async", return_value=True) as mock:
            r = client.post("/api/run", json={"only": ["Alpha", "Beta"]})
        assert r.status_code == 200
        assert r.json()["selective"] is True
        assert r.json()["sources"] == ["Alpha", "Beta"]
        mock.assert_called_once_with(only=["Alpha", "Beta"])

    def test_run_endpoint_full_run_when_no_body(self, client):
        with patch("src.scheduler.trigger_async", return_value=True) as mock:
            r = client.post("/api/run", json={})
        assert r.status_code == 200
        assert r.json()["selective"] is False
        mock.assert_called_once_with(only=None)

    def test_run_endpoint_409_when_already_running(self, client):
        with patch("src.scheduler.trigger_async", return_value=False):
            r = client.post("/api/run", json={})
        assert r.status_code == 409
