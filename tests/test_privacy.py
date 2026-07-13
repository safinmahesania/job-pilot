"""The privacy boundaries.

These are the tests that matter most, because a privacy regression is silent.
Nothing crashes when a phone number starts going to Gemini; the app keeps working
perfectly and you find out never. So each boundary gets a test that asserts on the
*actual prompt text* — not on a flag, not on a config value, but on what would go
over the wire.

The boundaries, in plain terms:

  1. In redacted mode (the default) no direct identifier appears in any prompt
     sent to a hosted model. Skills and work history do — a cover letter cannot be
     written without them. A name and a phone number cannot.
  2. In local mode nothing personal leaves the machine, and if the local model is
     down the call FAILS. It does not quietly fall back to the cloud.
  3. The finished document still carries the real name: the placeholder is
     substituted here, after the model is done.
  4. Scoring never needed identifiers, so it never sees them, in any mode.
  5. The autofill AI never sees contact details — those are matched by local rules.
  6. Tracking links from alert emails are not fetched when you've said not to.
"""
import pytest

from src import llm


class TestLocalOnlyMode:
    def test_personal_prompts_go_only_to_ollama(self, conn, monkeypatch,
                                                privacy_mode):
        privacy_mode("local")
        seen = []
        monkeypatch.setattr(llm, "_call_ollama",
                            lambda s, u: (seen.append("ollama"), ("answer", 5))[1])
        monkeypatch.setattr(llm, "_call_openai_compatible",
                            lambda p, s, u: (seen.append("CLOUD"), ("x", 1))[1])
        monkeypatch.setenv("GEMINI_API_KEY", "would-work-if-called")

        _, provider = llm.generate("sys", "my profile", personal=True)

        assert provider == "ollama"
        assert "CLOUD" not in seen

    def test_it_fails_closed_when_ollama_is_down(self, conn, monkeypatch,
                                                 privacy_mode):
        """The whole point. A fallback here would defeat the mode entirely."""
        privacy_mode("local")
        cloud_calls = []

        def dead(_s, _u):
            raise RuntimeError("ollama is not running")

        monkeypatch.setattr(llm, "_call_ollama", dead)
        monkeypatch.setattr(llm, "_call_openai_compatible",
                            lambda p, s, u: (cloud_calls.append(p), ("x", 1))[1])
        monkeypatch.setenv("GEMINI_API_KEY", "would-work-if-called")

        with pytest.raises(llm.LLMError):
            llm.generate("sys", "my profile", personal=True)

        assert cloud_calls == [], "local-only mode fell back to the cloud"

    def test_impersonal_prompts_still_use_the_chain(self, conn, monkeypatch,
                                                    privacy_mode):
        """A public job description is not personal data — don't cripple it."""
        privacy_mode("local")
        monkeypatch.setattr(llm, "_call_openai_compatible",
                            lambda p, s, u: ("x", 1))
        monkeypatch.setenv("GEMINI_API_KEY", "k")

        _, provider = llm.generate("sys", "a public job description")
        assert provider != "ollama"


class TestCoverLetterRedaction:
    def _generate(self, apply_mod, capture_llm):
        def reply(system, user):
            if "extract concrete requirements" in system:
                return ('["Python"]', "gemini")
            if "rank a candidate" in system:
                return ("[0]", "gemini")
            return ("Dear Hiring Manager,\n\n" + "x" * 260 +
                    "\n\nSincerely,\n{{NAME}}", "gemini")

        capture_llm.reply = reply
        return apply_mod.generate_cover_letter(
            {"title": "Backend Dev", "company": "Shopify",
             "description": "Python and FastAPI"}
        )

    def test_no_identifier_reaches_the_model(self, conn, monkeypatch, profile,
                                             identifiers, capture_llm,
                                             privacy_mode):
        privacy_mode("redacted")
        from src import apply
        monkeypatch.setattr(apply, "load_profile", lambda: profile)

        self._generate(apply, capture_llm)

        blob = "\n".join(capture_llm.all_prompts)
        leaked = [i for i in identifiers if i in blob]
        assert not leaked, f"identifiers sent to a hosted model: {leaked}"

    def test_the_finished_letter_still_has_the_real_name(self, conn, monkeypatch,
                                                         profile, capture_llm,
                                                         privacy_mode):
        """Redaction must not cost you a signature."""
        privacy_mode("redacted")
        from src import apply
        monkeypatch.setattr(apply, "load_profile", lambda: profile)

        result = self._generate(apply, capture_llm)

        assert "Safin Mahesania" in result["text"]
        assert "{{NAME}}" not in result["text"], "a placeholder survived into the output"

    def test_full_mode_does_send_identifiers(self, conn, monkeypatch, profile,
                                             capture_llm, privacy_mode):
        """Not a bug — the mode exists so the choice is visibly yours."""
        privacy_mode("full")
        from src import apply
        monkeypatch.setattr(apply, "load_profile", lambda: profile)

        self._generate(apply, capture_llm)

        assert "Safin Mahesania" in "\n".join(capture_llm.all_prompts)


class TestScoringPrompt:
    def test_carries_no_identifiers_in_any_mode(self, profile, identifiers):
        """Scoring never needed a name, so it never gets one."""
        from src.scoring.rerank import _candidate_summary

        summary = _candidate_summary(profile)

        leaked = [i for i in identifiers if i in summary]
        assert not leaked, f"scoring prompt leaked: {leaked}"

    def test_but_it_does_carry_the_skills(self, profile):
        """The negative test alone would pass on an empty string."""
        from src.scoring.rerank import _candidate_summary
        summary = _candidate_summary(profile)
        assert "Python" in summary
        assert "Acme" in summary


class TestAutofillPrompt:
    def test_contact_details_never_reach_the_model(self, conn, monkeypatch,
                                                   profile, identifiers,
                                                   capture_llm, privacy_mode):
        """Name, email and phone are matched by local rules — no AI call needed,
        so no reason to put them in a prompt."""
        privacy_mode("redacted")
        from src import autofill
        monkeypatch.setattr(autofill, "load_profile", lambda: profile)
        capture_llm.reply = lambda s, u: ('{"f0": "Yes"}', "gemini")

        autofill.resolve([{"id": "f0", "label": "Why do you want to work here?",
                           "type": "textarea", "options": []}])

        blob = "\n".join(capture_llm.all_prompts)
        leaked = [i for i in identifiers if i in blob]
        assert not leaked, f"autofill leaked: {leaked}"

    def test_it_still_gets_the_work_history(self, conn, monkeypatch, profile,
                                            capture_llm, privacy_mode):
        privacy_mode("redacted")
        from src import autofill
        monkeypatch.setattr(autofill, "load_profile", lambda: profile)
        capture_llm.reply = lambda s, u: ('{"f0": "Yes"}', "gemini")

        autofill.resolve([{"id": "f0", "label": "Why us?", "type": "textarea",
                           "options": []}])

        blob = "\n".join(capture_llm.all_prompts)
        assert "Python" in blob        # it can't answer without this


class TestTrackingLinks:
    def _no_network(self, monkeypatch, importers):
        requested = []

        class FakeHTTP:
            @staticmethod
            def get(url, **_kwargs):
                requested.append(url)
                raise RuntimeError("no network in tests")

        monkeypatch.setattr(importers, "httpx", FakeHTTP)
        return requested

    def test_trackers_are_not_fetched_when_disabled(self, conn, monkeypatch):
        from src import importers
        requested = self._no_network(monkeypatch, importers)
        monkeypatch.setattr(importers, "follow_links_enabled", lambda: False)

        for url in ["https://www.linkedin.com/comm/jobs/view/1?trk=eml",
                    "https://ca.indeed.com/rc/clk?jk=1",
                    "https://click.appcast.io/track/xyz"]:
            importers.recover_description(url)

        assert requested == [], f"tracking links were fetched: {requested}"

    def test_ats_links_are_always_fetched(self, conn, monkeypatch):
        """The setting is about trackers, not about refusing to read job pages."""
        from src import importers
        requested = self._no_network(monkeypatch, importers)
        monkeypatch.setattr(importers, "follow_links_enabled", lambda: False)

        importers.recover_description("https://boards.greenhouse.io/x/jobs/1")

        assert len(requested) == 1


class TestNoMailCredentials:
    def test_there_is_no_imap_client(self):
        """It was removed on purpose: an app password reads the whole account.

        If someone reintroduces IMAP, this fails and they have to justify it.
        """
        from pathlib import Path
        source = (Path(__file__).resolve().parent.parent
                  / "src" / "importers.py").read_text(encoding="utf-8")

        assert "imaplib" not in source
        assert "IMAP_PASSWORD" not in source
