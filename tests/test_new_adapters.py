"""Tests for the SmartRecruiters and Workable adapters, and the URL->ATS detector.

The adapters use fixed JSON rather than the network: they lock in that a posting is
mapped to the pipeline's shape (title, location, url) and that pagination stops. The
detector is a pure string function — no network — so it's tested directly.
"""
from unittest.mock import MagicMock, patch

from src.adapters.base import KNOWN_ATS, get_adapter
from src.adapters.smartrecruiters import SmartRecruitersAdapter
from src.adapters.workable import WorkableAdapter


def _resp(payload):
    r = MagicMock()
    r.json = lambda: payload
    r.raise_for_status = lambda: None
    return r


class TestSmartRecruiters:
    def test_maps_a_posting(self):
        payload = {"content": [{
            "name": "Flutter Developer",
            "location": {"city": "Toronto", "country": "CA"},
            "id": "abc123", "ref": "https://jobs.smartrecruiters.com/Co/abc123",
            "releasedDate": "2026-07-01",
        }]}
        with patch("httpx.get", return_value=_resp(payload)):
            jobs = SmartRecruitersAdapter({"name": "Co", "ats": "smartrecruiters",
                                           "identifier": "Co"}).fetch()
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Flutter Developer"
        assert jobs[0]["location"] == "Toronto, CA"
        assert jobs[0]["source"] == "smartrecruiters"

    def test_remote_flag_marks_location(self):
        payload = {"content": [{"name": "X", "location": {"city": "", "country": "CA",
                    "remote": True}, "id": "1"}]}
        with patch("httpx.get", return_value=_resp(payload)):
            jobs = SmartRecruitersAdapter({"name": "Co", "ats": "smartrecruiters",
                                           "identifier": "Co"}).fetch()
        assert "Remote" in jobs[0]["location"]

    def test_empty_content_stops_cleanly(self):
        with patch("httpx.get", return_value=_resp({"content": []})):
            jobs = SmartRecruitersAdapter({"name": "Co", "ats": "smartrecruiters",
                                           "identifier": "Co"}).fetch()
        assert jobs == []


class TestWorkable:
    def test_maps_a_job(self):
        payload = {"jobs": [{
            "title": "Python Engineer", "city": "Remote", "country": "CA",
            "shortcode": "ABC", "url": "https://apply.workable.com/co/j/ABC/",
            "employment_type": "Full-time", "published_on": "2026-07-01",
        }]}
        with patch("httpx.get", return_value=_resp(payload)):
            jobs = WorkableAdapter({"name": "Co", "ats": "workable",
                                    "identifier": "co"}).fetch()
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Python Engineer"
        assert jobs[0]["source"] == "workable"
        assert "ABC" in jobs[0]["source_url"]

    def test_builds_url_from_shortcode_when_missing(self):
        payload = {"jobs": [{"title": "X", "shortcode": "ZZZ"}]}
        with patch("httpx.get", return_value=_resp(payload)):
            jobs = WorkableAdapter({"name": "Co", "ats": "workable",
                                    "identifier": "co"}).fetch()
        assert "ZZZ" in jobs[0]["source_url"]


class TestRegistry:
    def test_new_adapters_are_registered(self):
        assert isinstance(get_adapter({"name": "A", "ats": "smartrecruiters",
                                       "identifier": "a"}), SmartRecruitersAdapter)
        assert isinstance(get_adapter({"name": "B", "ats": "workable",
                                       "identifier": "b"}), WorkableAdapter)

    def test_new_ats_are_known(self):
        assert {"smartrecruiters", "workable"} <= KNOWN_ATS


class TestDetect:
    CASES = [
        ("https://boards.greenhouse.io/stripe", "greenhouse", "stripe"),
        ("https://jobs.lever.co/figma", "lever", "figma"),
        ("https://jobs.ashbyhq.com/notion", "ashby", "notion"),
        ("https://jobs.smartrecruiters.com/Visa", "smartrecruiters", "Visa"),
        ("https://acme.workable.com", "workable", "acme"),
        ("https://apply.workable.com/acme", "workable", "acme"),
    ]

    def test_known_boards_detected(self, client):
        for url, ats, ident in self.CASES:
            d = client.post("/api/sources/detect", json={"url": url}).json()
            assert d["ats"] == ats, f"{url} -> {d['ats']} (wanted {ats})"
            assert d["identifier"] == ident, f"{url} id {d['identifier']} (wanted {ident})"

    def test_workday_url_flags_needs_detail(self, client):
        d = client.post("/api/sources/detect", json={
            "url": "https://td.wd3.myworkdayjobs.com/en-US/TD_Bank_Careers"}).json()
        assert d["ats"] == "workday"
        assert d["needs_detail"] is True
        assert d["tenant"] == "td"

    def test_unknown_url_falls_back_to_custom(self, client):
        d = client.post("/api/sources/detect", json={
            "url": "https://randomco.com/careers"}).json()
        assert d["ats"] == "custom"
        assert d["careers_url"] == "https://randomco.com/careers"

    def test_empty_url_is_400(self, client):
        assert client.post("/api/sources/detect", json={"url": ""}).status_code == 400
