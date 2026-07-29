"""A provider that rate-limits is rested for the batch, not retried once per job.

On a free tier the per-minute quota is spent in the first few jobs of a batch. Without
memory, every job after that still tries the hosted provider, gets a 429, and falls back —
the same result, but slower and buried in identical warnings. So a 429 rests the provider:
the rest of the batch skips it and goes straight to the fallback until the cooldown lifts.
"""
import time

import pytest

from src import llm


@pytest.fixture(autouse=True)
def _clear_cooldown():
    llm._rested_until.clear()
    yield
    llm._rested_until.clear()


class TestRecognisingARateLimit:
    def test_a_429_is_a_rate_limit(self):
        assert llm._is_rate_limit(Exception("Client error '429 Too Many Requests'"))

    def test_too_many_requests_text_counts(self):
        assert llm._is_rate_limit(Exception("Too Many Requests"))

    def test_an_ordinary_error_is_not_a_rate_limit(self):
        assert not llm._is_rate_limit(Exception("connection refused"))
        assert not llm._is_rate_limit(Exception("500 internal server error"))


class TestResting:
    def test_a_fresh_provider_is_not_resting(self):
        assert llm._resting("gemini") is False

    def test_resting_after_a_rest(self):
        llm._rest("gemini")
        assert llm._resting("gemini") is True

    def test_the_cooldown_expires(self):
        llm._rest("gemini")
        llm._rested_until["gemini"] = time.time() - 1      # pretend it elapsed
        assert llm._resting("gemini") is False

    def test_resting_one_provider_does_not_rest_another(self):
        llm._rest("gemini")
        assert llm._resting("gemini") is True
        assert llm._resting("cerebras") is False


class TestTheChainSkipsARestingProvider:
    def test_a_rested_provider_is_passed_over(self, monkeypatch):
        """gemini is resting, so the chain should reach cerebras without calling gemini."""
        monkeypatch.setattr(llm, "get_order", lambda: ["gemini", "cerebras"])
        monkeypatch.setattr(llm, "get_disabled", lambda: set())
        monkeypatch.setattr(llm, "is_configured", lambda n: True)
        monkeypatch.setattr(llm, "privacy_mode", lambda: "redacted")
        monkeypatch.setattr(llm, "record_usage", lambda *a: None)

        called = []

        def _fake_openai(name, system, user):
            called.append(name)
            if name == "gemini":
                raise AssertionError("a resting provider was called")
            return "the answer", 10

        monkeypatch.setattr(llm, "_call_openai_compatible", _fake_openai)
        llm._rest("gemini")

        text, provider = llm.generate("sys", "user")
        assert provider == "cerebras"
        assert "gemini" not in called

    def test_a_429_rests_the_provider_for_next_time(self, monkeypatch):
        monkeypatch.setattr(llm, "get_order", lambda: ["gemini", "cerebras"])
        monkeypatch.setattr(llm, "get_disabled", lambda: set())
        monkeypatch.setattr(llm, "is_configured", lambda n: True)
        monkeypatch.setattr(llm, "privacy_mode", lambda: "redacted")
        monkeypatch.setattr(llm, "record_usage", lambda *a: None)

        def _fake_openai(name, system, user):
            if name == "gemini":
                raise Exception("Client error '429 Too Many Requests'")
            return "answer", 10

        monkeypatch.setattr(llm, "_call_openai_compatible", _fake_openai)

        llm.generate("sys", "user")                 # first call trips the 429
        assert llm._resting("gemini") is True       # and rests it for the batch
