"""Tests for the generic HTML careers-page adapter (custom / aggregator / successfactors).

The adapter is best-effort HTML scraping, so these tests use fixed HTML rather than the
network: they lock in the rules that matter — real job links are kept, navigation/social/
filter links are dropped, off-site links are excluded for company pages, and the three
ats values all route here.
"""
import pytest
from unittest.mock import MagicMock, patch

from src.adapters.base import KNOWN_ATS, get_adapter
from src.adapters.generic import GenericCareersAdapter


def _mock_page(html, url="https://careers.example.com/jobs"):
    resp = MagicMock()
    resp.text = html
    resp.url = url
    resp.raise_for_status = lambda: None
    return resp


CAREERS_HTML = """
<html><body>
  <nav><a href="/login">Login</a><a href="/privacy">Privacy Policy</a></nav>
  <ul>
    <li><a href="/job/toronto-flutter-developer-1">Flutter Developer</a>
        <span class="job-location">Toronto, ON</span></li>
    <li><a href="/job/remote-python-engineer-2">Python Engineer</a></li>
    <li><a href="/careers/data-scientist-3">Data Scientist</a></li>
  </ul>
  <a href="https://facebook.com/co">Facebook</a>
  <a href="/categories">Categories</a>
  <a href="/search?q=dev">Search</a>
</body></html>
"""


class TestGenericAdapter:
    def test_extracts_real_job_links_only(self):
        with patch("httpx.get", return_value=_mock_page(CAREERS_HTML)):
            a = GenericCareersAdapter({"name": "Example", "ats": "custom",
                                       "careers_url": "https://careers.example.com/jobs"})
            jobs = a.fetch()
        titles = {j["title"] for j in jobs}
        assert titles == {"Flutter Developer", "Python Engineer", "Data Scientist"}

    def test_navigation_and_social_are_dropped(self):
        with patch("httpx.get", return_value=_mock_page(CAREERS_HTML)):
            a = GenericCareersAdapter({"name": "Example", "ats": "custom",
                                       "careers_url": "https://careers.example.com/jobs"})
            jobs = a.fetch()
        titles = {j["title"] for j in jobs}
        for junk in ("Login", "Privacy Policy", "Facebook", "Categories", "Search"):
            assert junk not in titles

    def test_location_is_picked_up_when_present(self):
        with patch("httpx.get", return_value=_mock_page(CAREERS_HTML)):
            a = GenericCareersAdapter({"name": "Example", "ats": "custom",
                                       "careers_url": "https://careers.example.com/jobs"})
            jobs = a.fetch()
        flutter = next(j for j in jobs if j["title"] == "Flutter Developer")
        assert "Toronto" in flutter["location"]

    def test_links_are_made_absolute(self):
        with patch("httpx.get", return_value=_mock_page(CAREERS_HTML)):
            a = GenericCareersAdapter({"name": "Example", "ats": "custom",
                                       "careers_url": "https://careers.example.com/jobs"})
            jobs = a.fetch()
        assert all(j["source_url"].startswith("https://careers.example.com/") for j in jobs)

    def test_off_site_links_excluded_for_company_pages(self):
        html = """<a href="https://other-site.com/job/x-1">Off-site Job</a>
                  <a href="/job/local-2">Local Job</a>"""
        with patch("httpx.get", return_value=_mock_page(html)):
            a = GenericCareersAdapter({"name": "Co", "ats": "custom",
                                       "careers_url": "https://careers.example.com/jobs"})
            jobs = a.fetch()
        titles = {j["title"] for j in jobs}
        assert "Local Job" in titles
        assert "Off-site Job" not in titles          # custom page: stay on host

    def test_off_site_links_allowed_for_aggregators(self):
        html = """<a href="https://company-a.com/job/x-1">Aggregated Job</a>"""
        with patch("httpx.get", return_value=_mock_page(html,
                   url="https://talent.com/jobs")):
            a = GenericCareersAdapter({"name": "Talent", "ats": "aggregator",
                                       "careers_url": "https://talent.com/jobs"})
            jobs = a.fetch()
        assert any(j["title"] == "Aggregated Job" for j in jobs)

    def test_missing_careers_url_is_an_error_not_an_empty_board(self):
        """A source with no URL can never work, and must not look like one that simply
        had no openings. Returning [] made it report success forever: the health check
        counts a quiet zero separately from a thrown error precisely so a setup mistake
        can be told apart from an empty day, and this one belongs in the error column."""
        a = GenericCareersAdapter({"name": "Broken", "ats": "custom"})
        with pytest.raises(RuntimeError, match="careers_url"):
            a.fetch()

    def test_a_site_that_cannot_be_read_is_an_error_not_an_empty_board(self):
        """Same distinction, other cause. A careers page that answers 403 to every
        request is not a board with nothing on it — and a year of that used to show as
        "has never returned a job", which points the reader at the slug when the real
        answer is that the site refuses us."""
        with patch("httpx.get", side_effect=Exception("network down")):
            a = GenericCareersAdapter({"name": "Down", "ats": "custom",
                                       "careers_url": "https://x.com/careers"})
            with pytest.raises(RuntimeError, match="could not read"):
                a.fetch()

    def test_a_page_that_reads_but_matches_nothing_is_still_an_empty_board(self):
        """The other side of it: the site answered, it just had no job links. That is a
        real zero and must stay one, or every quiet board becomes a false alarm."""
        class _Resp:
            status_code = 200
            text = "<html><body><a href='/about'>About us</a></body></html>"
            url = "https://x.com/careers"

            def raise_for_status(self):
                return None

        with patch("httpx.get", return_value=_Resp()):
            a = GenericCareersAdapter({"name": "Quiet", "ats": "custom",
                                       "careers_url": "https://x.com/careers"})
            assert a.fetch() == []


class TestRegistryRouting:
    def test_three_ats_values_route_to_generic(self):
        for ats in ("custom", "aggregator", "successfactors"):
            company = {"name": f"T {ats}", "ats": ats,
                       "careers_url": "https://x.com/careers"}
            assert isinstance(get_adapter(company), GenericCareersAdapter)

    def test_all_three_are_known_ats(self):
        assert {"custom", "aggregator", "successfactors"} <= KNOWN_ATS
