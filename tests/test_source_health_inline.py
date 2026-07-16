"""Tests for source health surfaced inline in /api/sources/config.

Each configured source should carry its own last-run health — the verdict, fetched/kept
counts, and detail line — matched by name. A source with no run yet carries health=None,
not an error.
"""
import sqlite3

from src import configio


def _seed_health(db):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO source_health (name, ats, fetched, kept, status, error, "
                 "error_streak, zero_streak) VALUES "
                 "('1Password', 'ashby', 63, 0, 'ok', NULL, 0, 0)")
    conn.execute("INSERT INTO source_health (name, ats, fetched, kept, status, error, "
                 "error_streak, zero_streak) VALUES "
                 "('Acme', 'greenhouse', 0, 0, 'error', '404 Not Found', 3, 0)")
    conn.commit()
    conn.close()


def _mock_companies(monkeypatch, companies):
    monkeypatch.setattr(configio, "read_yaml",
                        lambda name: {"companies": companies} if "companies" in name else {})


class TestSourceHealthInConfig:
    def test_healthy_source_carries_ok_verdict_and_counts(self, client, db, monkeypatch):
        _seed_health(db)
        _mock_companies(monkeypatch, [
            {"name": "1Password", "ats": "ashby", "identifier": "1password", "active": True}])
        row = client.get("/api/sources/config").json()[0]
        assert row["health"]["verdict"] == "ok"
        assert row["health"]["fetched"] == 63
        assert row["health"]["kept"] == 0

    def test_broken_source_carries_erroring_verdict_and_detail(self, client, db, monkeypatch):
        _seed_health(db)
        _mock_companies(monkeypatch, [
            {"name": "Acme", "ats": "greenhouse", "identifier": "acme", "active": True}])
        row = client.get("/api/sources/config").json()[0]
        assert row["health"]["verdict"] == "erroring"
        assert "404" in row["health"]["detail"]

    def test_source_with_no_run_has_null_health(self, client, db, monkeypatch):
        # No source_health row for this one -> health is None, not an error.
        _mock_companies(monkeypatch, [
            {"name": "BrandNew", "ats": "lever", "identifier": "new", "active": True}])
        row = client.get("/api/sources/config").json()[0]
        assert row["health"] is None

    def test_config_still_returns_core_fields(self, client, db, monkeypatch):
        _mock_companies(monkeypatch, [
            {"name": "X", "ats": "lever", "identifier": "x", "active": False}])
        row = client.get("/api/sources/config").json()[0]
        assert row["name"] == "X"
        assert row["ats"] == "lever"
        assert row["active"] is False
        assert row["index"] == 0
