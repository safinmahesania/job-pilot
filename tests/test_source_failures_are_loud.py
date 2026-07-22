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
            status_code = 200
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
            status_code = 200
            def raise_for_status(self): return None
            def json(self): return {"data": []}

        with patch("httpx.get", return_value=_R()):
            assert self._adapter().fetch() == []


class TestJSearchPicksTheRightHost:
    """RapidAPI and OpenWeb Ninja serve the same API behind different hosts and auth
    headers. Sending a key to the wrong one returns 401 — which reads as a bad key, and
    sends you off to regenerate a key that was fine. The key's own shape says which host
    issued it, so nothing has to be remembered."""

    def _adapter(self, **extra):
        from src.adapters.jsearch import JSearchAdapter
        return JSearchAdapter({"name": "JS", "ats": "jsearch",
                               "queries": ["developer"], **extra})

    def _host_used(self, monkeypatch, key, **extra):
        monkeypatch.setenv("JSEARCH_API_KEY", key)
        seen = {}

        def _fake(url, **kw):
            seen["url"] = url
            seen["headers"] = kw.get("headers", {})
            raise Exception("stop here")

        with patch("httpx.get", side_effect=_fake):
            with pytest.raises(RuntimeError):
                self._adapter(**extra).fetch()
        return seen

    def test_a_rapidapi_shaped_key_goes_to_rapidapi(self, monkeypatch):
        seen = self._host_used(
            monkeypatch, "8f4a2c9d1emsh3b7e6f5a4c2d1b0p1a9b8cjsn7e6d5c4b3a2f")
        assert "rapidapi.com" in seen["url"]
        assert "X-RapidAPI-Key" in seen["headers"]

    def test_another_shape_goes_to_openweb_ninja(self, monkeypatch):
        seen = self._host_used(monkeypatch, "f1e2d3c4-b5a6-7890-1234-56789abcdef0")
        assert "openwebninja.com" in seen["url"]
        assert "x-api-key" in seen["headers"]

    def test_an_explicit_host_overrides_the_guess(self, monkeypatch):
        """The shape is a default, not a rule — a source can still say."""
        seen = self._host_used(
            monkeypatch, "8f4a2c9d1emsh3b7e6f5a4c2d1b0p1a9b8cjsn7e6d5c4b3a2f",
            host="openwebninja")
        assert "openwebninja.com" in seen["url"]

    def test_the_failure_names_the_host_it_tried(self, monkeypatch):
        monkeypatch.setenv("JSEARCH_API_KEY", "f1e2d3c4-b5a6-7890-1234-56789abcdef0")
        with patch("httpx.get", side_effect=Exception("401 Unauthorized")):
            with pytest.raises(RuntimeError, match="OpenWeb Ninja"):
                self._adapter().fetch()


class TestTheServersOwnExplanationIsKept:
    """A status code names the category; the body names the cause. RapidAPI answers 403
    with "You are not subscribed to this API" — throwing that away leaves a bare status
    that could mean six things."""

    def test_the_response_body_is_included(self):
        from src.adapters.jsearch import _why

        class _R:
            text = '{"message":"You are not subscribed to this API."}'

        out = _why(_R(), "Client error '403 Forbidden'")
        assert "not subscribed" in out
        assert "403" in out

    def test_credentials_in_the_body_are_still_redacted(self):
        from src.adapters.jsearch import _why

        class _R:
            text = '{"echo":"api_key=supersecret"}'

        assert "supersecret" not in _why(_R(), "boom")

    def test_a_body_that_cannot_be_read_is_not_fatal(self):
        from src.adapters.jsearch import _why

        class _R:
            @property
            def text(self):
                raise ValueError("stream consumed")

        assert "boom" in _why(_R(), "boom")

    def test_a_timeout_has_no_response_to_read_and_still_reports(self):
        """A timeout or DNS failure never got a response, so there is no body. The
        adapters fall back to the exception alone rather than crashing on None."""
        from src.adapters.jsearch import JSearchAdapter
        import os
        os.environ["JSEARCH_API_KEY"] = "f1e2d3c4-b5a6-7890-1234-56789abcdef0"
        with patch("httpx.get", side_effect=TimeoutError("read timed out")):
            with pytest.raises(RuntimeError, match="timed out"):
                JSearchAdapter({"name": "JS", "ats": "jsearch",
                                "queries": ["dev"]}).fetch()


class TestJSearchUsesTheCurrentEndpoint:
    """The API moved to /search-v2 and page numbers gave way to cursors. The old path
    answers 404 — which reads as "your key is wrong" and is not."""

    def _run(self, pages_payload, **cfg):
        import os
        from src.adapters.jsearch import JSearchAdapter
        os.environ["JSEARCH_API_KEY"] = "f1e2d3c4-b5a6-7890-1234-56789abcdef0"
        calls = []

        class _R:
            status_code = 200
            def __init__(self, p): self._p = p
            def raise_for_status(self): return None
            def json(self): return self._p

        def _fake(url, params=None, headers=None, timeout=None):
            calls.append((url, dict(params or {})))
            return _R(pages_payload[len(calls) - 1])

        with patch("httpx.get", side_effect=_fake):
            jobs = JSearchAdapter({"name": "JS", "ats": "jsearch",
                                   "queries": ["developer"], **cfg}).fetch()
        return calls, jobs

    def test_it_calls_search_v2(self):
        calls, _ = self._run([{"data": [], "cursor": None}])
        assert calls[0][0].endswith("/search-v2")

    def test_no_page_number_is_sent(self):
        """search-v2 has no page parameter; sending one is not how it paginates."""
        calls, _ = self._run([{"data": [], "cursor": None}])
        assert "page" not in calls[0][1]
        assert "num_pages" not in calls[0][1]

    def test_the_location_goes_inside_the_query(self):
        """"developer in montreal" returns what "developer" alone does not."""
        calls, _ = self._run([{"data": [], "cursor": None}], location="Montreal")
        assert calls[0][1]["query"] == "developer in Montreal"

    def test_the_cursor_from_one_page_is_sent_with_the_next(self):
        job = {"job_id": "a", "job_title": "Dev", "employer_name": "X",
               "job_apply_link": "http://x/1"}
        calls, jobs = self._run(
            [{"data": [job], "cursor": "CUR2"},
             {"data": [dict(job, job_id="b")], "cursor": None}],
            pages=2)
        assert "cursor" not in calls[0][1]          # first request asks for no page
        assert calls[1][1]["cursor"] == "CUR2"
        assert len(jobs) == 2

    def test_paging_stops_when_no_cursor_comes_back(self):
        job = {"job_id": "a", "job_title": "Dev", "employer_name": "X",
               "job_apply_link": "http://x/1"}
        calls, _ = self._run([{"data": [job], "cursor": None}], pages=5)
        assert len(calls) == 1                     # nothing further to ask for


class TestJSearchFindsThePathItself:
    """This API has renamed its search endpoint more than once, and the two gateways
    have not always renamed together. A 404 on a stale path reads exactly like a bad
    key, and sends you to regenerate one that was fine."""

    JOB = {"job_id": "a", "job_title": "Dev", "employer_name": "X",
           "job_apply_link": "http://x/1"}

    class _R:
        def __init__(self, code, payload=None):
            self.status_code = code
            self._p = payload or {}
            self.text = "not found" if code == 404 else ""

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"Client error '{self.status_code}'")

        def json(self):
            return self._p

    def _run(self, working_path, **cfg):
        import os
        from src.adapters.jsearch import JSearchAdapter
        os.environ["JSEARCH_API_KEY"] = "7a1b2c3demsh1f2e3d4c5b6a7f8p1a2b3cjsn4d5e6f7a8"
        tried = []

        def _fake(url, params=None, headers=None, timeout=None):
            path = url.rsplit("/", 1)[1]
            tried.append(path)
            if path == working_path:
                return self._R(200, {"data": [self.JOB], "cursor": None})
            return self._R(404)

        with patch("httpx.get", side_effect=_fake):
            jobs = JSearchAdapter({"name": "JS", "ats": "jsearch",
                                   "queries": ["dev"], **cfg}).fetch()
        return tried, jobs

    def test_the_first_path_that_answers_is_used(self):
        tried, jobs = self._run("search-v2")
        assert tried == ["search-v2"]        # no pointless probing past a hit
        assert len(jobs) == 1

    def test_a_404_moves_on_to_the_next_candidate(self):
        tried, jobs = self._run("job-search")
        assert tried == ["search-v2", "job-search"]
        assert len(jobs) == 1

    def test_an_explicit_endpoint_is_not_second_guessed(self):
        tried, jobs = self._run("job-search", endpoint="job-search")
        assert tried == ["job-search"]

    def test_a_key_error_is_not_retried_across_paths(self):
        """401 is about the key and would be true at every path. Retrying it three
        times just triples the noise before the same answer."""
        import os
        from src.adapters.jsearch import JSearchAdapter
        os.environ["JSEARCH_API_KEY"] = "7a1b2c3demsh1f2e3d4c5b6a7f8p1a2b3cjsn4d5e6f7a8"
        tried = []

        def _fake(url, params=None, headers=None, timeout=None):
            tried.append(url.rsplit("/", 1)[1])
            return self._R(401)

        with patch("httpx.get", side_effect=_fake):
            with pytest.raises(RuntimeError):
                JSearchAdapter({"name": "JS", "ats": "jsearch",
                                "queries": ["dev"]}).fetch()
        assert tried == ["search-v2"]

    def test_when_nothing_answers_the_error_names_what_was_tried(self):
        import os
        from src.adapters.jsearch import JSearchAdapter
        os.environ["JSEARCH_API_KEY"] = "7a1b2c3demsh1f2e3d4c5b6a7f8p1a2b3cjsn4d5e6f7a8"

        with patch("httpx.get", side_effect=lambda *a, **k: self._R(404)):
            with pytest.raises(RuntimeError, match="search-v2"):
                JSearchAdapter({"name": "JS", "ats": "jsearch",
                                "queries": ["dev"]}).fetch()


class TestJSearchReadsBothResponseShapes:
    """The same API serves two shapes. The older one puts jobs straight under `data`;
    search-v2 nests them beside the paging cursor. Iterating the wrong one walks a
    dict's keys and dies on "'str' object has no attribute 'get'" — an error that says
    nothing about the response that caused it."""

    JOB = {"job_id": "a", "job_title": "Dev", "employer_name": "X",
           "job_apply_link": "http://x/1"}

    class _R:
        status_code = 200
        def __init__(self, p): self._p = p
        def raise_for_status(self): return None
        def json(self): return self._p

    def _fetch(self, payload):
        import os
        from src.adapters.jsearch import JSearchAdapter
        os.environ["JSEARCH_API_KEY"] = "7a1b2cmsh1f2e3d4c5b6a7f8p1a2b3cjsn4d5e6f7a8"
        with patch("httpx.get", return_value=self._R(payload)):
            return JSearchAdapter({"name": "JS", "ats": "jsearch",
                                   "queries": ["dev"]}).fetch()

    def test_the_nested_shape_is_read(self):
        jobs = self._fetch({"status": "OK",
                            "data": {"jobs": [self.JOB], "cursor": None}})
        assert len(jobs) == 1

    def test_the_flat_shape_is_still_read(self):
        jobs = self._fetch({"status": "OK", "data": [self.JOB]})
        assert len(jobs) == 1

    def test_an_unfamiliar_shape_is_named_not_silently_empty(self):
        """Zero jobs would report this as "no matches" when it is really a response we
        cannot read — the same quiet failure this whole file exists to remove."""
        with pytest.raises(RuntimeError, match="unfamiliar"):
            self._fetch({"status": "OK", "data": {"totally": "different"}})

    def test_non_dict_entries_are_skipped_rather_than_crashing(self):
        jobs = self._fetch({"status": "OK",
                            "data": {"jobs": ["oops", self.JOB], "cursor": None}})
        assert len(jobs) == 1

    def test_an_empty_job_list_is_no_matches_not_a_broken_shape(self):
        """`jobs: []` is the API working and saying there is nothing. Chaining the
        lookups with `or` made that falsy value fall through to the missing-key branch,
        so a correct empty answer was reported as a shape the code could not read."""
        assert self._fetch({"status": "OK",
                            "data": {"jobs": [], "cursor": None}}) == []


class TestQueriesAreNotRunTwice:
    """Hand-edited query lists grow duplicates. Each one costs a full round trip against
    an API that takes seconds per call, and returns rows discarded as already seen."""

    class _R:
        status_code = 200
        def raise_for_status(self): return None
        def json(self): return {"status": "OK", "data": {"jobs": [], "cursor": None}}

    def _queries_sent(self, queries):
        import os
        from src.adapters.jsearch import JSearchAdapter
        os.environ["JSEARCH_API_KEY"] = "7a1b2cmsh1f2e3d4c5b6a7f8p1a2b3cjsn4d5e6f7a8"
        sent = []

        def _fake(url, params=None, headers=None, timeout=None):
            sent.append((params or {}).get("query"))
            return self._R()

        with patch("httpx.get", side_effect=_fake):
            JSearchAdapter({"name": "JS", "ats": "jsearch", "queries": queries}).fetch()
        return sent

    def test_a_repeated_query_is_only_asked_once(self):
        assert self._queries_sent(["developer", "intern", "developer"]) == [
            "developer", "intern"]

    def test_the_written_order_is_kept(self):
        assert self._queries_sent(["zebra", "apple", "zebra", "mango"]) == [
            "zebra", "apple", "mango"]

    def test_blank_entries_are_dropped(self):
        assert self._queries_sent(["developer", "", "  "]) == ["developer"]


class TestAQuotaStopsTheSourceAtOnce:
    """429 is about the account, not the query. A monthly quota that has run out will
    not have refilled by the next keyword, so asking nine more times fills the log with
    the same paragraph nine times over — and where the limit is per-minute rather than
    monthly, digs the hole deeper."""

    class _R429:
        status_code = 429
        text = ('{"message":"You have exceeded the MONTHLY quota for Requests on '
                'your current plan, BASIC"}')
        def raise_for_status(self):
            raise Exception("Client error '429 Too Many Requests'")

    def _run(self, queries, pages=5):
        import os
        from src.adapters.jsearch import JSearchAdapter
        os.environ["JSEARCH_API_KEY"] = "7a1b2cmsh1f2e3d4c5b6a7f8p1a2b3cjsn4d5e6f7a8"
        calls = []

        def _fake(url, params=None, headers=None, timeout=None):
            calls.append((params or {}).get("query"))
            return self._R429()

        with patch("httpx.get", side_effect=_fake):
            with pytest.raises(RuntimeError) as excinfo:
                JSearchAdapter({"name": "JS", "ats": "jsearch",
                                "queries": queries, "pages": pages}).fetch()
        return calls, str(excinfo.value)

    def test_only_one_request_is_made(self):
        calls, _ = self._run(["a", "b", "c", "d", "e", "f", "g", "h", "i"])
        assert len(calls) == 1, f"kept going after a 429: {calls}"

    def test_the_remaining_pages_are_not_tried_either(self):
        calls, _ = self._run(["only one query"], pages=5)
        assert len(calls) == 1

    def test_it_does_not_send_you_hunting_for_a_broken_key(self):
        """The setup is fine. Telling someone to check their key and their endpoint
        here costs them an evening on a fault that isn't there."""
        _, msg = self._run(["a", "b"])
        assert "Nothing is wrong with the setup" in msg
        assert "401" not in msg and "404" not in msg

    def test_it_says_what_would_actually_help(self):
        _, msg = self._run(["a", "b"])
        assert "quota" in msg.lower()
        assert "active: false" in msg or "pages" in msg
