"""Fixes for issues found while using the app end-to-end as a real user.

Each of these was a real papercut caught by clicking through every feature, not by a
test — so each gets a test now, to keep it fixed.
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

import src.api as api


class TestAddingASourceIsValidated:
    """A source with no name or an unknown ats used to be written to companies.yaml,
    where it did nothing but produce a blank-named 'No adapter' error on the next
    fetch. Now the form refuses it."""

    def test_an_empty_name_is_rejected(self, client):
        r = client.post("/api/sources", json={"name": "", "ats": "greenhouse"})

        assert r.status_code == 422        # pydantic min_length

    def test_a_whitespace_only_name_is_rejected(self, client):
        r = client.post("/api/sources", json={"name": "   ", "ats": "lever"})

        assert r.status_code == 400

    def test_an_unknown_ats_is_rejected_with_the_valid_list(self, client):
        r = client.post("/api/sources", json={"name": "Acme", "ats": "greehnouse"})

        assert r.status_code == 400
        assert "greenhouse" in r.json()["detail"]      # tells you the right spelling

    def test_a_valid_source_is_accepted(self, client):
        r = client.post("/api/sources",
                        json={"name": "Acme", "ats": "greenhouse", "identifier": "acme"})

        assert r.status_code == 200
        assert r.json()["added"] == "Acme"

    def test_the_ats_is_normalised_to_lowercase(self, client):
        r = client.post("/api/sources",
                        json={"name": "B", "ats": "GREENHOUSE", "identifier": "b"})

        assert r.status_code == 200


class TestGenerationWithoutAProviderIsFriendly:
    """No API key and no Ollama is the most common failure for a new user. The raw
    'all providers failed -> gemini: not configured | ...' is accurate and useless;
    the message now says what to do."""

    def _a_job(self, client):
        # Insert a job directly so we have something to generate for.
        from src.paths import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO jobs (dedupe_hash, title, company, description, status, score) "
            "VALUES ('genjob', 'Dev', 'Acme', 'A Flutter role.', 'surfaced', 90)")
        conn.commit()
        jid = conn.execute(
            "SELECT id FROM jobs WHERE dedupe_hash='genjob'").fetchone()[0]
        conn.close()
        return jid

    def test_a_missing_provider_gives_503_and_actionable_text(self, client):
        jid = self._a_job(client)

        r = client.post(f"/api/jobs/{jid}/cover-letter")

        assert r.status_code == 503
        detail = r.json()["detail"].lower()
        assert "provider" in detail
        # points at the fix, not the stack trace
        assert "key" in detail or "ollama" in detail
        assert "traceback" not in detail


class TestAnUnknownTabIsEmptyNotTheFeed:
    def test_an_unknown_tab_returns_nothing(self, client):
        """It used to fall back to the feed, quietly showing the wrong list under the
        wrong heading. A tab the app does not define now returns an empty list."""
        r = client.get("/api/jobs?tab=totally-made-up")

        assert r.status_code == 200
        assert r.json() == []

    def test_the_real_tabs_still_work(self, client):
        for tab in ("feed", "saved", "applied", "dismissed", "unscored"):
            assert client.get(f"/api/jobs?tab={tab}").status_code == 200
