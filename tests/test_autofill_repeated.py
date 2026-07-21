"""Your history as lists, so a form can ask for it more than once.

A Workday form where you press "Add" three times has three Job Title boxes carrying
the same label. Answered from a flat dict, all three came out identical — the same
employer, the same dates, three times over — and the same happened for education.
"""
from unittest.mock import patch

from src import autofill

PROFILE = {
    "identity": {"name": "Test User"},
    "experience": [
        {"company": "Second Co", "role": "Developer", "start": "2024", "end": "2025"},
        {"company": "First Co", "role": "Intern", "start": "2023", "end": "2024"},
    ],
    "education": [
        {"institution": "Concordia", "degree": "MSc", "field": "Computer Science"},
        {"institution": "Earlier U", "degree": "BSc", "field": "Software"},
    ],
}


def _repeated(profile):
    with patch.object(autofill, "load_profile", return_value=profile):
        return autofill.repeated()


class TestExperienceIsAList:
    def test_every_job_is_returned_not_just_the_first(self):
        assert len(_repeated(PROFILE)["experience"]) == 2

    def test_the_order_is_the_order_you_wrote(self):
        exp = _repeated(PROFILE)["experience"]
        assert exp[0]["company"] == "Second Co"
        assert exp[1]["company"] == "First Co"

    def test_role_is_carried_as_title(self):
        """The form asks for a job title; the profile calls it a role."""
        assert _repeated(PROFILE)["experience"][0]["title"] == "Developer"

    def test_an_entry_with_neither_company_nor_title_is_dropped(self):
        """A blank row would fill a section with nothing, which is worse than leaving
        the section empty."""
        out = _repeated({"experience": [{"start": "2020"}, {"company": "Real Co"}]})
        assert len(out["experience"]) == 1
        assert out["experience"][0]["company"] == "Real Co"


class TestEducationIsAList:
    def test_every_school_is_returned(self):
        assert len(_repeated(PROFILE)["education"]) == 2

    def test_institution_is_carried_as_school(self):
        assert _repeated(PROFILE)["education"][0]["school"] == "Concordia"

    def test_a_blank_entry_is_dropped(self):
        out = _repeated({"education": [{}, {"institution": "Concordia"}]})
        assert len(out["education"]) == 1


class TestItSurvivesAThinProfile:
    def test_no_history_at_all_is_empty_lists_not_an_error(self):
        out = _repeated({"identity": {"name": "X"}})
        assert out == {"experience": [], "education": []}

    def test_malformed_entries_are_skipped(self):
        out = _repeated({"experience": ["not a dict", {"company": "Real Co"}]})
        assert len(out["experience"]) == 1


class TestTheEndpointCarriesIt:
    def test_autofill_data_includes_the_lists(self, client):
        body = client.get("/api/autofill/data").json()
        assert "repeated" in body
        assert "experience" in body["repeated"]
        assert "education" in body["repeated"]

    def test_the_flat_answers_are_still_there(self):
        """The extension still uses them for everything that isn't a repeat."""
        with patch.object(autofill, "load_profile", return_value=PROFILE):
            flat = autofill.answers()
        assert flat["current_company"] == "Second Co"
