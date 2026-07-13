"""Autofill: what the extension puts into an application form.

Two things this must never do.

It must never answer a voluntary demographic question. Race, gender, disability
and veteran status are optional by law and the form says so. A tool that fills
them in "helpfully" has made a disclosure decision on your behalf that you cannot
take back. The correct behaviour is to leave them alone and let you decide, every
time.

And it must never invent an answer to something factual. If your profile doesn't
say whether you need sponsorship, the field stays empty and you fill it in. A
guess here is a lie on a legal document.
"""
import pytest

from src import autofill


class TestCanonicalAnswers:
    def test_it_derives_the_obvious_ones_from_your_profile(self, conn,
                                                           monkeypatch, profile):
        monkeypatch.setattr(autofill, "load_profile", lambda: profile)

        data = autofill.answers()

        assert data["first_name"] == "Safin"
        assert data["email"] == "safin@example.com"
        assert data["phone"] == "+1 514 555 0123"

    def test_a_field_your_profile_does_not_answer_is_left_empty(self, conn,
                                                                monkeypatch,
                                                                profile):
        """Blank, not guessed. You will see the gap and fill it."""
        profile["application"]["gender"] = ""
        monkeypatch.setattr(autofill, "load_profile", lambda: profile)

        data = autofill.answers()

        assert not data.get("gender")


class TestCustomAnswers:
    def test_your_own_answer_beats_anything_the_model_would_say(self, conn,
                                                                monkeypatch,
                                                                profile,
                                                                capture_llm):
        """You wrote it once; it should never be re-invented."""
        monkeypatch.setattr(autofill, "load_profile", lambda: profile)
        capture_llm.reply = lambda s, u: ('{"f0": "AI would say something else"}',
                                          "gemini")

        result = autofill.resolve([{
            "id": "f0",
            "label": "Do you have a criminal record?",
            "type": "text", "options": [],
        }])

        assert result["f0"] == "No"

    def test_the_model_is_not_even_asked_when_a_custom_answer_matches(
            self, conn, monkeypatch, profile, capture_llm):
        monkeypatch.setattr(autofill, "load_profile", lambda: profile)

        autofill.resolve([{"id": "f0", "label": "Any criminal record?",
                           "type": "text", "options": []}])

        assert capture_llm.calls == [], "an AI call was made for a settled answer"


class TestWorkArrangement:
    def test_the_model_is_given_the_facts_it_needs_to_answer(self, conn,
                                                             monkeypatch,
                                                             profile,
                                                             capture_llm):
        """The extension's local rules answer most of these without a model. When
        one slips through, the model must at least have the arrangement facts in
        front of it — otherwise it will guess, and a guess about your willingness
        to relocate is a lie on a real application."""
        monkeypatch.setattr(autofill, "load_profile", lambda: profile)
        capture_llm.reply = lambda s, u: ('{"f0": "Yes"}', "gemini")

        autofill.resolve([{
            "id": "f0",
            "label": "Are you comfortable with this arrangement?",
            "type": "select", "options": ["Yes", "No"],
        }])

        prompt = "\n".join(capture_llm.all_prompts)
        assert "work_arrangement" in prompt
        assert "max_days_onsite_per_week" in prompt


class TestVoluntaryDisclosures:
    @pytest.mark.parametrize("label", [
        "Race / Ethnicity (voluntary)",
        "Gender identity — optional",
        "Do you identify as a person with a disability?",
        "Veteran status (voluntary self-identification)",
    ])
    def test_they_are_never_answered(self, conn, monkeypatch, profile,
                                     capture_llm, label):
        """These are yours to disclose or not. A tool must not decide for you."""
        monkeypatch.setattr(autofill, "load_profile", lambda: profile)
        capture_llm.reply = lambda s, u: ('{"f0": "Prefer not to say"}', "gemini")

        result = autofill.resolve([{"id": "f0", "label": label,
                                    "type": "select",
                                    "options": ["Yes", "No", "Prefer not to say"]}])

        assert not result.get("f0"), (
            f"autofill answered a voluntary disclosure: {label!r}"
        )
