"""Tests for the JSearch adapter (keyword search across Google for Jobs).

Fixed JSON, no network. Locks in: multiple keywords each produce a query, jobs are
mapped to the pipeline shape, duplicates across queries are de-duped, the remote flag is
read, and a missing API key fails with a clear message.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from src.adapters.base import KNOWN_ATS, get_adapter
from src.adapters.jsearch import JSearchAdapter


def _mock_get(payload_for):
    """Return an httpx.get stand-in that answers per-query from payload_for(kw)."""
    def _get(url, params=None, headers=None, timeout=None):
        resp = MagicMock()
        kw = (params or {}).get("query", "")
        resp.json = lambda: {"data": payload_for(kw)}
        resp.raise_for_status = lambda: None
        return resp
    return _get


def _job(jid, title, remote=False):
    return {"job_id": jid, "job_title": title, "employer_name": "Co",
            "job_city": "Toronto", "job_state": "ON", "job_country": "CA",
            "job_apply_link": f"https://x.com/{jid}", "job_description": "desc",
            "job_employment_type": "FULLTIME", "job_is_remote": remote}


class TestJSearch:
    def setup_method(self):
        os.environ["JSEARCH_API_KEY"] = "test_key"

    def test_multiple_queries_each_fetch(self):
        payload = lambda kw: [_job(f"{kw}-1", f"{kw} role")]
        a = JSearchAdapter({"name": "S", "ats": "jsearch",
                            "queries": ["developer", "qa", "devops"]})
        with patch("httpx.get", side_effect=_mock_get(payload)):
            jobs = a.fetch()
        titles = {j["title"] for j in jobs}
        assert titles == {"developer role", "qa role", "devops role"}

    def test_single_query_string_also_works(self):
        payload = lambda kw: [_job("1", "Dev")]
        a = JSearchAdapter({"name": "S", "ats": "jsearch", "query": "developer"})
        with patch("httpx.get", side_effect=_mock_get(payload)):
            jobs = a.fetch()
        assert len(jobs) == 1

    def test_duplicate_jobs_across_queries_are_deduped(self):
        # Same job_id returned for two different keywords -> counted once.
        payload = lambda kw: [_job("same-id", "Shared role")]
        a = JSearchAdapter({"name": "S", "ats": "jsearch",
                            "queries": ["developer", "engineer"]})
        with patch("httpx.get", side_effect=_mock_get(payload)):
            jobs = a.fetch()
        assert len(jobs) == 1

    def test_remote_flag_marks_location(self):
        payload = lambda kw: [_job("r1", "Remote dev", remote=True)]
        a = JSearchAdapter({"name": "S", "ats": "jsearch", "queries": ["dev"]})
        with patch("httpx.get", side_effect=_mock_get(payload)):
            jobs = a.fetch()
        assert "Remote" in jobs[0]["location"]
        assert jobs[0]["remote"] == 1

    def test_maps_core_fields(self):
        payload = lambda kw: [_job("1", "Backend Dev")]
        a = JSearchAdapter({"name": "S", "ats": "jsearch", "queries": ["dev"]})
        with patch("httpx.get", side_effect=_mock_get(payload)):
            job = a.fetch()[0]
        assert job["source"] == "jsearch"
        assert job["title"] == "Backend Dev"
        assert job["company"] == "Co"
        assert "Toronto" in job["location"]

    def test_missing_key_raises_clear_error(self):
        os.environ.pop("JSEARCH_API_KEY", None)
        a = JSearchAdapter({"name": "S", "ats": "jsearch", "queries": ["dev"]})
        with pytest.raises(RuntimeError, match="JSEARCH_API_KEY"):
            a.fetch()

    def test_fetch_error_on_one_query_doesnt_crash(self):
        os.environ["JSEARCH_API_KEY"] = "test_key"

        def _get(url, params=None, headers=None, timeout=None):
            if "bad" in params["query"]:
                raise Exception("500 error")
            resp = MagicMock()
            resp.json = lambda: {"data": [_job("g1", "Good role")]}
            resp.raise_for_status = lambda: None
            return resp

        a = JSearchAdapter({"name": "S", "ats": "jsearch",
                            "queries": ["good", "bad"]})
        with patch("httpx.get", side_effect=_get):
            jobs = a.fetch()
        # the good query still returns; the bad one is skipped, not fatal
        assert any(j["title"] == "Good role" for j in jobs)


class TestRegistry:
    def test_jsearch_is_registered(self):
        os.environ["JSEARCH_API_KEY"] = "k"
        a = get_adapter({"name": "S", "ats": "jsearch", "queries": ["dev"]})
        assert isinstance(a, JSearchAdapter)

    def test_jsearch_is_known(self):
        assert "jsearch" in KNOWN_ATS
