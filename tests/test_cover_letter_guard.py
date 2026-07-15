"""The cover letter's fabrication guard.

The resume was made safe by construction — it selects from your background and can
only reorder what is already true. The cover letter still writes prose, which is
exactly where invention moves once the resume closes the door: "in my three years
with React and AWS..." reads perfectly, is a lie, and goes to a named human who can
check. This guard holds the letter to the same standard as the resume summary: every
technology it names must be one the profile actually contains.
"""
import pytest

from src import resume_guard


PROFILE = {
    "identity": {"name": "Test User"},
    "summary": "A junior developer.",
    "skills": {
        "expert": ["Flutter", "Dart", "Python"],
        "proficient": ["Java", "SQL", "Firebase"],
    },
    "skill_categories": [
        {"label": "Databases", "skills": ["MySQL", "SQLite"]},
    ],
    "projects": [
        {"name": "Mobile App", "tech": ["Flutter", "Firebase"], "highlights": []},
    ],
}


class TestATechnologyNotInTheProfileIsCaught:
    def test_an_invented_framework_is_flagged(self):
        text = "I have deep experience with React and Redux."

        problems = resume_guard.check_cover_letter_prose(text, PROFILE)

        assert any("React" in p for p in problems)

    def test_an_invented_cloud_is_flagged(self):
        text = "I deployed Kubernetes clusters on AWS."

        problems = resume_guard.check_cover_letter_prose(text, PROFILE)

        joined = " ".join(problems)
        assert "Kubernetes" in joined
        assert "AWS" in joined

    def test_several_inventions_are_all_reported(self):
        text = "Skilled in Angular, Vue, and Rust."

        problems = resume_guard.check_cover_letter_prose(text, PROFILE)

        assert len(problems) == 3


class TestWhatIsInTheProfilePasses:
    def test_a_letter_using_only_real_skills_is_clean(self):
        text = ("I am a Flutter and Dart developer. I have built apps with Firebase "
                "and Python, backed by SQL databases.")

        problems = resume_guard.check_cover_letter_prose(text, PROFILE)

        assert problems == []

    def test_a_substring_skill_is_recognised(self):
        """SQLite is in the profile; a letter mentioning SQL should not be flagged
        just because the exact string differs."""
        text = "Comfortable writing SQL against relational databases."

        problems = resume_guard.check_cover_letter_prose(text, PROFILE)

        assert problems == []


class TestTheTargetCompanyIsNotFabrication:
    def test_the_company_you_apply_to_is_allowed(self):
        """A cover letter names the company it is addressed to. That is not a claim
        about your background and must never read as an invented tool."""
        text = "I would be thrilled to bring my Flutter skills to Datadog."

        problems = resume_guard.check_cover_letter_prose(
            text, PROFILE, target_company="Datadog")

        assert problems == []

    def test_without_the_company_arg_a_capitalised_company_could_flag(self):
        """Sanity: the exclusion is doing work — the same letter without the company
        passed in would otherwise treat the name as an unknown token. (Datadog is not
        in the profile, so it would be flagged.)"""
        text = "I would love to bring my Flutter skills to Datadog."

        problems = resume_guard.check_cover_letter_prose(text, PROFILE)

        assert any("Datadog" in p for p in problems)


class TestYourOwnNameIsNotATechnology:
    def test_the_applicants_name_never_flags(self):
        text = "Test User is a Flutter developer."

        problems = resume_guard.check_cover_letter_prose(text, PROFILE)

        assert not any("Test" in p or "User" in p for p in problems)
