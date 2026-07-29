"""Fetching the full description for an Adzuna job — but only from where it is safe to.

Adzuna's API returns a truncated snippet; the full text is on the page it links to. Three
destinations are followed: Adzuna's own detail page, and the public JSON APIs of Lever and
Greenhouse. Everything else — Workday, LinkedIn, a company careers page — is left alone,
so the job keeps its snippet and stays unscored rather than being judged on half its text.
"""
from unittest.mock import patch

from src import enrich

FULL = (
    "We are hiring a Junior Python Developer for our billing team in Toronto. You will "
    "build and maintain backend services, write tests, review pull requests and ship to "
    "production. Required: Python, SQL, and a willingness to learn. Competitive salary, "
    "health benefits, hybrid schedule. Apply below and tell us why this interests you. "
) * 3


def _job(url, desc="short snippet", source="adzuna"):
    return {"source": source, "title": "Dev", "company": "Acme",
            "description": desc, "source_url": url, "apply_url": url}


class _Resp:
    def __init__(self, *, text="", json_data=None, status=200):
        self.text = text
        self._json = json_data
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise Exception(f"HTTP {self._status}")

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


class TestOnlyTheThreeAllowedDestinations:
    def test_a_lever_link_is_enrichable(self):
        job = _job("https://jobs.lever.co/acme/abc123de-4567-89ab-cdef-0123456789ab")
        assert enrich.is_enrichable(job) is True

    def test_a_greenhouse_link_is_enrichable(self):
        job = _job("https://boards.greenhouse.io/acme/jobs/4567890")
        assert enrich.is_enrichable(job) is True

    def test_an_adzuna_link_is_enrichable(self):
        job = _job("https://www.adzuna.ca/land/ad/12345")
        assert enrich.is_enrichable(job) is True

    def test_a_workday_link_is_left_alone(self):
        job = _job("https://acme.wd1.myworkdayjobs.com/careers/job/123")
        assert enrich.is_enrichable(job) is False

    def test_a_linkedin_link_is_left_alone(self):
        job = _job("https://www.linkedin.com/jobs/view/123456")
        assert enrich.is_enrichable(job) is False

    def test_a_random_company_site_is_left_alone(self):
        job = _job("https://careers.somecompany.com/jobs/senior-dev")
        assert enrich.is_enrichable(job) is False

    def test_a_non_adzuna_source_is_never_enriched(self):
        """Lever's own adapter already brings full text; this is only for Adzuna jobs
        whose link happens to point at Lever."""
        job = _job("https://jobs.lever.co/acme/abc123de-4567-89ab-cdef-0123456789ab",
                   source="lever")
        assert enrich.is_enrichable(job) is False


class TestLeverUsesTheJsonApi:
    def test_it_calls_the_api_not_the_html_page(self):
        job = _job("https://jobs.lever.co/acme/abc123de-4567-89ab-cdef-0123456789ab")
        seen = {}

        def _fake(url, **k):
            seen["url"] = url
            return _Resp(json_data={"descriptionPlain": FULL, "lists": []})

        with patch("httpx.get", side_effect=_fake):
            changed = enrich.enrich_if_needed(job)
        assert changed is True
        assert seen["url"].startswith("https://api.lever.co/v0/postings/acme/")
        assert "billing team" in job["description"]

    def test_lists_are_folded_in(self):
        job = _job("https://jobs.lever.co/acme/abc123de-4567-89ab-cdef-0123456789ab")
        payload = {"descriptionPlain": "Intro. " * 30,
                   "lists": [{"text": "Requirement one. " * 20}]}
        with patch("httpx.get", return_value=_Resp(json_data=payload)):
            enrich.enrich_if_needed(job)
        assert "Requirement one" in job["description"]


class TestGreenhouseUsesTheJsonApi:
    def test_it_calls_the_boards_api(self):
        job = _job("https://boards.greenhouse.io/acme/jobs/4567890")
        seen = {}

        def _fake(url, **k):
            seen["url"] = url
            return _Resp(json_data={"content": f"<p>{FULL}</p>"})

        with patch("httpx.get", side_effect=_fake):
            changed = enrich.enrich_if_needed(job)
        assert changed is True
        assert "boards-api.greenhouse.io/v1/boards/acme/jobs/4567890" in seen["url"]
        assert "billing team" in job["description"]


class TestAdzunaFallsBackToHtml:
    def test_the_largest_text_block_is_taken(self):
        job = _job("https://www.adzuna.ca/land/ad/12345")
        html = f"<html><body><nav>Menu</nav><div>{FULL}</div></body></html>"
        with patch("httpx.get", return_value=_Resp(text=html)):
            changed = enrich.enrich_if_needed(job)
        assert changed is True
        assert "billing team" in job["description"]


class TestWhenItCannotEnrich:
    def test_a_failed_fetch_keeps_the_snippet(self):
        job = _job("https://jobs.lever.co/acme/abc123de-4567-89ab-cdef-0123456789ab",
                   desc="original snippet")
        with patch("httpx.get", side_effect=Exception("connection reset")):
            assert enrich.enrich_if_needed(job) is False
        assert job["description"] == "original snippet"

    def test_a_short_response_is_not_used(self):
        job = _job("https://boards.greenhouse.io/acme/jobs/4567890", desc="original")
        with patch("httpx.get", return_value=_Resp(json_data={"content": "<p>tiny</p>"})):
            assert enrich.enrich_if_needed(job) is False
        assert job["description"] == "original"

    def test_a_disallowed_url_makes_no_request(self):
        job = _job("https://acme.wd1.myworkdayjobs.com/careers/job/123")
        with patch("httpx.get") as get:
            enrich.enrich_if_needed(job)
        get.assert_not_called()

    def test_an_already_full_snippet_is_not_refetched(self):
        job = _job("https://jobs.lever.co/acme/abc123de-4567-89ab-cdef-0123456789ab",
                   desc="x" * 500)
        with patch("httpx.get") as get:
            assert enrich.enrich_if_needed(job) is False
        get.assert_not_called()
