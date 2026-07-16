"""Tests for the Adzuna adapter (keyword search over aggregated listings).

Fixed JSON, no network. Locks in: multiple keywords each search, salary min/max are
carried through, jobs map to the pipeline shape, duplicates are de-duped, and a missing
app_id/app_key fails with a clear message.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from src.adapters.adzuna import AdzunaAdapter
from src.adapters.base import KNOWN_ATS, get_adapter


def _mock_get(payload_for):
    def _get(url, params=None, timeout=None):
        resp = MagicMock()
        kw = (params or {}).get("what", "")
        resp.json = lambda: {"results": payload_for(kw)}
        resp.raise_for_status = lambda: None
        return resp
    return _get


def _job(jid, title, salary_min=None, salary_max=None):
    return {"id": jid, "title": title,
            "company": {"display_name": "Co"},
            "location": {"display_name": "Toronto, ON"},
            "redirect_url": f"https://x.com/{jid}", "description": "desc",
            "created": "2026-07-01", "salary_min": salary_min,
            "salary_max": salary_max, "contract_time": "full_time"}


class TestAdzuna:
    def setup_method(self):
        os.environ["ADZUNA_APP_ID"] = "id"
        os.environ["ADZUNA_APP_KEY"] = "key"

    def test_multiple_queries_each_search(self):
        payload = lambda kw: [_job(f"{kw}-1", f"{kw} role")]
        a = AdzunaAdapter({"name": "S", "ats": "adzuna",
                           "queries": ["developer", "engineer"]})
        with patch("httpx.get", side_effect=_mock_get(payload)):
            jobs = a.fetch()
        assert {j["title"] for j in jobs} == {"developer role", "engineer role"}

    def test_salary_is_carried_through(self):
        payload = lambda kw: [_job("1", "Dev", salary_min=70000, salary_max=90000)]
        a = AdzunaAdapter({"name": "S", "ats": "adzuna", "queries": ["dev"]})
        with patch("httpx.get", side_effect=_mock_get(payload)):
            job = a.fetch()[0]
        assert job["salary_min"] == 70000
        assert job["salary_max"] == 90000

    def test_maps_core_fields(self):
        payload = lambda kw: [_job("1", "Backend Dev")]
        a = AdzunaAdapter({"name": "S", "ats": "adzuna", "queries": ["dev"]})
        with patch("httpx.get", side_effect=_mock_get(payload)):
            job = a.fetch()[0]
        assert job["source"] == "adzuna"
        assert job["title"] == "Backend Dev"
        assert job["company"] == "Co"
        assert "Toronto" in job["location"]
        assert job["job_type"] == "Full-time"

    def test_duplicates_are_deduped(self):
        payload = lambda kw: [_job("same", "Shared")]
        a = AdzunaAdapter({"name": "S", "ats": "adzuna",
                           "queries": ["developer", "engineer"]})
        with patch("httpx.get", side_effect=_mock_get(payload)):
            jobs = a.fetch()
        assert len(jobs) == 1

    def test_missing_credentials_raises_clear_error(self):
        os.environ.pop("ADZUNA_APP_ID", None)
        os.environ.pop("ADZUNA_APP_KEY", None)
        a = AdzunaAdapter({"name": "S", "ats": "adzuna", "queries": ["dev"]})
        with pytest.raises(RuntimeError, match="ADZUNA_APP_ID"):
            a.fetch()

    def test_one_query_failing_does_not_crash(self):
        os.environ["ADZUNA_APP_ID"] = "id"
        os.environ["ADZUNA_APP_KEY"] = "key"

        def _get(url, params=None, timeout=None):
            if "bad" in params["what"]:
                raise Exception("429 rate limited")
            resp = MagicMock()
            resp.json = lambda: {"results": [_job("g", "Good")]}
            resp.raise_for_status = lambda: None
            return resp

        a = AdzunaAdapter({"name": "S", "ats": "adzuna",
                           "queries": ["good", "bad"]})
        with patch("httpx.get", side_effect=_get):
            jobs = a.fetch()
        assert any(j["title"] == "Good" for j in jobs)


class TestRegistry:
    def test_adzuna_registered(self):
        os.environ["ADZUNA_APP_ID"] = "id"
        os.environ["ADZUNA_APP_KEY"] = "key"
        a = get_adapter({"name": "S", "ats": "adzuna", "queries": ["dev"]})
        assert isinstance(a, AdzunaAdapter)

    def test_adzuna_is_known(self):
        assert "adzuna" in KNOWN_ATS
