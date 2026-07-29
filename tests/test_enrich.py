"""Fetching the full description for an Adzuna job before it is scored.

Adzuna's API returns a truncated snippet; the full posting is on the page it links to.
Scoring on the snippet is scoring half a job, so for an Adzuna job about to be scored the
full text is fetched — and when it can't be, the job is left unscored rather than judged
on half of what it says.
"""
from unittest.mock import patch

from src import enrich

FULL_POSTING = (
    "We are hiring a Junior Python Developer to join our billing team in Toronto. "
    "You will build and maintain backend services, write tests, review pull requests "
    "and ship to production. Required: Python, SQL, and a willingness to learn. We offer "
    "a competitive salary, health benefits, and a hybrid schedule. Apply through the "
    "link below and tell us why this role interests you. " * 3
)


def _adzuna_job(desc="short snippet", url="https://adzuna.example/land/1"):
    return {"source": "adzuna", "title": "Dev", "company": "Acme",
            "description": desc, "source_url": url, "apply_url": url}


class _Resp:
    def __init__(self, text, status=200):
        self.text = text
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise Exception(f"HTTP {self._status}")


class TestOnlyAdzunaIsEnriched:
    def test_an_adzuna_job_with_a_link_is_enrichable(self):
        assert enrich.is_enrichable(_adzuna_job()) is True

    def test_a_company_board_job_is_left_alone(self):
        job = {"source": "greenhouse", "source_url": "https://x", "description": "s"}
        assert enrich.is_enrichable(job) is False

    def test_a_linkedin_job_is_left_alone(self):
        job = {"source": "linkedin", "apply_url": "https://x", "description": "s"}
        assert enrich.is_enrichable(job) is False

    def test_an_adzuna_job_with_no_link_is_not_enrichable(self):
        job = {"source": "adzuna", "description": "s"}
        assert enrich.is_enrichable(job) is False


class TestFetchingTheFullText:
    def test_the_snippet_is_replaced_with_the_full_posting(self):
        job = _adzuna_job()
        html = f"<html><body><div>{FULL_POSTING}</div></body></html>"
        with patch("httpx.get", return_value=_Resp(html)):
            changed = enrich.enrich_if_needed(job)
        assert changed is True
        assert "billing team" in job["description"]
        assert len(job["description"]) > 400

    def test_a_failed_fetch_leaves_the_snippet_and_returns_false(self):
        job = _adzuna_job(desc="the original snippet")
        with patch("httpx.get", side_effect=Exception("connection reset")):
            changed = enrich.enrich_if_needed(job)
        assert changed is False
        assert job["description"] == "the original snippet"

    def test_a_page_too_short_to_be_a_posting_is_not_used(self):
        """A cookie wall or an expired-job stub is shorter than a real description; the
        snippet we already had is the better of two bad options."""
        job = _adzuna_job(desc="the original snippet")
        with patch("httpx.get", return_value=_Resp("<html>Please accept cookies</html>")):
            changed = enrich.enrich_if_needed(job)
        assert changed is False
        assert job["description"] == "the original snippet"

    def test_an_http_error_is_swallowed_not_raised(self):
        job = _adzuna_job()
        with patch("httpx.get", return_value=_Resp("", status=503)):
            assert enrich.enrich_if_needed(job) is False


class TestItDoesNotWasteRequests:
    def test_an_already_full_description_is_not_refetched(self):
        """If Adzuna returned something substantial for once, spend no request."""
        job = _adzuna_job(desc="x" * 500)
        with patch("httpx.get") as get:
            changed = enrich.enrich_if_needed(job)
        assert changed is False
        get.assert_not_called()

    def test_a_non_adzuna_job_makes_no_request(self):
        job = {"source": "linkedin", "apply_url": "https://x", "description": "s"}
        with patch("httpx.get") as get:
            enrich.enrich_if_needed(job)
        get.assert_not_called()


class TestFullDescriptionReturnsNoneCleanly:
    def test_no_url_is_none_not_a_crash(self):
        assert enrich.full_description({"source": "adzuna"}) is None

    def test_a_short_page_is_none(self):
        job = _adzuna_job()
        with patch("httpx.get", return_value=_Resp("<html>tiny</html>")):
            assert enrich.full_description(job) is None
