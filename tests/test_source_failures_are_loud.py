"""A source that cannot work must not report success.

The health check counts a thrown error separately from a quiet zero, so that a board
with nothing on it today can be told apart from one that has never worked. Adapters
that swallowed their own failures defeated that: a wrong API key, a 403, a missing URL
all arrived as "0 jobs", and the Admin tab could only guess — it suggested checking the
slug when the real answer was a 401.
"""
import pytest
from unittest.mock import patch

from src.adapters.base import redact


class TestCredentialsNeverReachTheLogs:
    """httpx puts the request URL in the exception it raises, and that URL carries the
    key. Logging it verbatim writes credentials into a file people paste into chats."""

    def test_query_string_secrets_are_stripped(self):
        text = ("Client error '401' for url 'https://api.adzuna.com/v1/api/jobs/ca/"
                "search/1?app_id=abc123&app_key=supersecretvalue&what=developer'")
        out = redact(text)
        assert "supersecretvalue" not in out
        assert "abc123" not in out
        assert "401" in out            # the useful part survives

    def test_other_parameters_are_left_alone(self):
        assert "what=developer" in redact("...?app_key=zzz&what=developer")

    def test_it_handles_non_strings(self):
        assert redact(ValueError("token=abcdef")) == "token=***"


class TestAdzunaFailsLoudly:
    def _adapter(self):
        from src.adapters.adzuna import AdzunaAdapter
        return AdzunaAdapter({"name": "Adzuna CA", "ats": "adzuna",
                              "queries": ["developer"], "country": "ca"})

    def test_every_request_failing_raises_rather_than_returning_empty(self, monkeypatch):
        monkeypatch.setenv("ADZUNA_APP_ID", "id")
        monkeypatch.setenv("ADZUNA_APP_KEY", "key")
        with patch("httpx.get", side_effect=Exception("401 Unauthorized")):
            with pytest.raises(RuntimeError, match="every Adzuna request failed"):
                self._adapter().fetch()

    def test_a_real_empty_result_is_still_empty(self, monkeypatch):
        """The API answered, it just had no matches. That is a genuine zero."""
        monkeypatch.setenv("ADZUNA_APP_ID", "id")
        monkeypatch.setenv("ADZUNA_APP_KEY", "key")

        class _R:
            def raise_for_status(self): return None
            def json(self): return {"results": []}

        with patch("httpx.get", return_value=_R()):
            assert self._adapter().fetch() == []


class TestJSearchFailsLoudly:
    def _adapter(self):
        from src.adapters.jsearch import JSearchAdapter
        return JSearchAdapter({"name": "JSearch CA", "ats": "jsearch",
                               "queries": ["developer"]})

    def test_every_request_failing_raises(self, monkeypatch):
        monkeypatch.setenv("JSEARCH_API_KEY", "k")
        with patch("httpx.get", side_effect=Exception("401 Unauthorized")):
            with pytest.raises(RuntimeError, match="every JSearch request failed"):
                self._adapter().fetch()

    def test_a_real_empty_result_is_still_empty(self, monkeypatch):
        monkeypatch.setenv("JSEARCH_API_KEY", "k")

        class _R:
            def raise_for_status(self): return None
            def json(self): return {"data": []}

        with patch("httpx.get", return_value=_R()):
            assert self._adapter().fetch() == []
