"""Fixes for issues found while using the app end-to-end as a real user.

Each of these was a real papercut caught by clicking through every feature, not by a
test — so each gets a test now, to keep it fixed.
"""
import sqlite3




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

    def test_a_missing_provider_gives_503_and_actionable_text(
            self, client, monkeypatch):
        # The unit under test is the endpoint's error MAPPING, not the generator. Force
        # the generator to fail exactly the way a missing provider fails — an LLMError —
        # and assert the endpoint turns that into a friendly 503 rather than a raw 502.
        # (Driving it through the real generator would depend on whether a profile.yaml
        # and a provider happen to exist in the environment, which is not what this
        # test is about.)
        from src.llm import LLMError

        def _no_provider(*_a, **_k):
            raise LLMError(
                "all providers failed -> gemini: not configured | "
                "cerebras: not configured | ollama: not running")

        monkeypatch.setattr("src.apply.generate_cover_letter", _no_provider)

        jid = self._a_job(client)
        r = client.post(f"/api/jobs/{jid}/cover-letter")

        assert r.status_code == 503
        detail = r.json()["detail"].lower()
        assert "provider" in detail
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


class TestUploadsAreSizeCapped:
    """An import endpoint that reads the whole body into memory with no limit is a
    one-line denial of service: a single large POST exhausts memory. The cap rejects
    anything oversized with a 413 before it is fully buffered."""

    def test_a_small_file_is_not_rejected_for_size(self, client, written_profile):
        """A file under the cap must not get a 413. Whether the import then succeeds
        or 400s on content is a different concern — this test is only about size."""
        csv = (b"title,company,location,apply_url,description\n"
               b"Dev,Acme,Remote,http://x,A Flutter role\n")

        r = client.post("/api/import/file",
                        files={"file": ("jobs.csv", csv, "text/csv")})

        assert r.status_code != 413

    def test_an_oversized_file_is_refused_with_413(self, client):
        from src.paths import MAX_UPLOAD_BYTES

        oversized = b"title,company\n" + b"x,y\n" * (MAX_UPLOAD_BYTES // 3)

        r = client.post("/api/import/file",
                        files={"file": ("big.csv", oversized, "text/csv")})

        assert r.status_code == 413
        assert "too large" in r.json()["detail"].lower()


class TestExpensiveEndpointsAreRateLimited:
    """The generation and import endpoints each cost an LLM call or a file parse. On a
    public tunnel they are the expensive surface, so they carry per-route limits a real
    user never reaches and a bot does."""

    def test_hammering_cover_letter_eventually_429s(self, client, written_profile,
                                                    monkeypatch):
        import sqlite3
        from src.paths import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO jobs (dedupe_hash, title, company, description, status, score) "
            "VALUES ('rl', 'Dev', 'Acme', 'A Flutter role.', 'surfaced', 90)")
        conn.commit()
        jid = conn.execute("SELECT id FROM jobs WHERE dedupe_hash='rl'").fetchone()[0]
        conn.close()

        # This test is about the rate limiter, not the model — so the generation itself
        # must be instant and offline. On a machine with real provider keys in .env,
        # each un-stubbed call would go out to Gemini/Cerebras over the network and take
        # seconds; the 30 calls would then spread across more than the limiter's
        # 60-second window, so the 20/minute cap would never be reached within any one
        # window and the test would fail for a reason that has nothing to do with the
        # limiter. Stubbing the call keeps every request cheap so the burst actually
        # hits the cap, on any machine, keys or no keys.
        import src.apply as apply

        def _instant(_job):
            return {"text": "stub", "provider": "test"}

        monkeypatch.setattr(apply, "generate_cover_letter", _instant)

        # The generation limit is 20/minute; well within a burst of 30.
        saw_429 = False
        for _ in range(30):
            r = client.post(f"/api/jobs/{jid}/cover-letter")
            if r.status_code == 429:
                saw_429 = True
                break
        assert saw_429

    def test_a_normal_number_of_requests_is_fine(self, client, written_profile):
        # A handful of imports in a row must never be rate-limited.
        for _ in range(5):
            r = client.post("/api/import/text", json={"text": "x"})
            assert r.status_code != 429
