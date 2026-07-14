"""The model returns numbers. It cannot lie with a number.

Every fabrication this project produced came from the model writing something:

    an invented employer          — it was writing the experience section
    a degree from a university
      it never attended           — it was writing the education section
    "Proficient in React"         — it was writing the summary
    "(No work experience listed)" — it was writing, and chose to write nothing

And every section where it was not asked to write has never once produced a lie.
The skills section takes category labels and fills their contents from
profile.yaml. It has no guard. It has never needed one.

So the model stopped writing. It is handed the profile, numbered, and it returns
numbers — which jobs in which order, which of their bullets, which projects, which
skills first — and the code assembles the page from the profile.

The tests in this file do not check that a lie is CAUGHT. They check that it cannot
be told. An index into a list of three employers cannot name a fourth. A bullet
copied from profile.yaml cannot claim a framework you have never used. There is no
guard here because there is nothing to guard, and that is the entire point of the
design.

What is still checked, and has its own tests: the summary. It is the one place the
model still writes, because it is the one place where writing earns its keep — and
so it is the one place a lie can still enter.
"""
import json

import pytest

from src import resume_select
from src.resume_select import MalformedResume, parse, resolve


PROFILE = {
    "identity": {"name": "Safin Mahesania"},
    "contact": {"city": "Montreal", "province": "Quebec"},
    "summary": "Software developer across frontend and backend.",
    "skill_categories": [
        {"label": "Programming", "skills": ["Java", "Python"]},
        {"label": "Databases", "skills": ["MySQL", "SQLite"]},
    ],
    "experience": [
        {"role": "Teaching Assistant", "company": "Concordia University",
         "location": "Montreal, QC", "start": "2026-01", "end": "2026-04",
         "highlights": ["Supported students in Java and OOP.",
                        "Created structured learning materials.",
                        "Reviewed student code and guided debugging.",
                        "Adapted explanations to different learning styles."]},
        {"role": "Flutter Developer", "company": "Otrack", "location": "Remote",
         "start": "2024-01", "end": "2024-11",
         "highlights": ["Built a Flutter app with API-driven data.",
                        "Applied MVC architecture for a scalable codebase."]},
        {"role": "Support Officer", "company": "Bank Alfalah",
         "location": "Karachi, PK", "start": "2021-06", "end": "2022-08",
         "highlights": ["Troubleshot banking applications using logs and SQL."]},
    ],
    "education": [
        {"degree": "MSc Computer Science", "institution": "Concordia University",
         "location": "Montreal, QC", "start": "2025-01", "end": "2026-08"},
        {"degree": "BSc Computer Science", "institution": "SZABIST",
         "location": "Karachi, PK", "start": "2018-01", "end": "2022-06"},
    ],
    "projects": [
        {"name": "Plant Disease Detection", "tech": ["PyTorch"], "link": "",
         "highlights": ["Built a classifier on MobileNetV3-Small.",
                        "Designed the augmentation pipeline in PyTorch."]},
        {"name": "Recipedia", "tech": ["Flutter"], "link": "",
         "highlights": ["Built a cross-platform app that scans produce."]},
        {"name": "Risk Game", "tech": ["Java"], "link": "",
         "highlights": ["Built a turn-based strategy game."]},
        {"name": "NewsApp", "tech": ["Kotlin"], "link": "",
         "highlights": ["Built an Android news app."]},
    ],
    "certificates": [{"name": "Azure Fundamentals", "date": "2023-06", "link": ""}],
    "volunteer": [{"organization": "Al-Azhar Garden Student's Association",
                   "role": "STEM Co-Lead",
                   "description": "Organised STEM-focused events."}],
}


class TestAnInventedIndexPointsAtNothing:
    """The old design caught an invented employer and refused the resume. This one
    does not catch it, because it cannot happen.

    That is a stronger claim, and it is worth being precise about the difference: a
    guard is a promise that something will be noticed. This is a fact about what can
    be expressed. The model has no way to say "TechCorp Inc." — the only thing it
    can say about employers is a number, and the numbers all point at real jobs.
    """

    def _evil(self):
        """The model tries every lie it has ever told."""
        return {
            "summary": "Junior software developer with Java and SQL experience.",
            "skills": [1, 0],
            "experience": [
                {"job": 0, "bullets": [0, 2]},
                {"job": 1, "bullets": [0, 1]},
                {"job": 99, "bullets": [0]},          # an employer you never had
            ],
            "education": [0, 1, 47],                  # a university you never attended
            "projects": [{"project": 2, "bullets": [0]},
                         {"project": 1, "bullets": [0]},
                         {"project": 88, "bullets": [0]}],   # a project you never built
            "certificates": [0, 5],                   # a certificate you never earned
            "volunteer": [0],
        }

    def test_the_invented_employer_simply_is_not_there(self):
        resume = resolve(self._evil(), PROFILE)

        companies = [e["company"] for e in resume["experience"]]

        assert companies == ["Concordia University", "Otrack", "Bank Alfalah"]

    def test_no_error_is_raised_because_nothing_went_wrong(self):
        """There is no FabricationError here, and no retry. An index the model made
        up is not a fact it made up — it is a number that does not point at
        anything, and it disappears."""
        resolve(self._evil(), PROFILE)          # no exception

    def test_the_invented_university_is_not_there(self):
        resume = resolve(self._evil(), PROFILE)

        schools = [e["institution"] for e in resume["education"]]

        assert schools == ["Concordia University", "SZABIST"]

    def test_the_invented_project_is_not_there(self):
        resume = resolve(self._evil(), PROFILE)

        names = [p["name"] for p in resume["projects"]]

        assert set(names) <= {p["name"] for p in PROFILE["projects"]}

    def test_the_invented_certificate_is_not_there(self):
        resume = resolve(self._evil(), PROFILE)

        assert [c["name"] for c in resume["certificates"]] == ["Azure Fundamentals"]

    def test_garbage_where_a_number_belonged(self):
        """A string, a null, a nested object. All of them point at nothing."""
        selection = {"summary": "Developer.",
                     "experience": [{"job": "TechCorp Inc.", "bullets": [0]},
                                    {"job": None, "bullets": [0]},
                                    {"job": {"name": "Acme"}, "bullets": [0]}]}

        resume = resolve(selection, PROFILE)

        assert [e["company"] for e in resume["experience"]] == \
            ["Concordia University", "Otrack", "Bank Alfalah"]


class TestNothingRealCanBeDropped:
    """The other half of the old failure. Told it had invented an employer, the
    model deleted the entire work history and wrote "(No work experience listed)"
    for someone with three real jobs.

    It cannot now. The code renders every job in the profile; the model's numbers
    decide only the order.
    """

    def test_a_job_the_model_forgot_is_rendered_anyway(self):
        selection = {"summary": "Developer.",
                     "experience": [{"job": 1, "bullets": [0]}]}   # only Otrack

        resume = resolve(selection, PROFILE)

        companies = [e["company"] for e in resume["experience"]]

        assert companies == ["Otrack", "Concordia University", "Bank Alfalah"]
        assert len(companies) == 3

    def test_an_empty_selection_still_produces_a_whole_resume(self):
        """The model returns nothing useful. Every real thing still appears."""
        resume = resolve({"summary": "Developer."}, PROFILE)

        assert len(resume["experience"]) == 3
        assert len(resume["education"]) == 2
        assert len(resume["certificates"]) == 1
        assert len(resume["volunteer"]) == 1
        assert len(resume["skills"]) == 2

    def test_a_skill_category_the_model_forgot_still_appears(self):
        resume = resolve({"summary": "x", "skills": [1]}, PROFILE)

        labels = [g["label"] for g in resume["skills"]]

        assert labels == ["Databases", "Programming"]

    def test_the_education_the_model_forgot_still_appears(self):
        resume = resolve({"summary": "x", "education": [1]}, PROFILE)

        assert [e["institution"] for e in resume["education"]] == \
            ["SZABIST", "Concordia University"]


class TestTheBulletsAreYours:
    """A bullet on the resume is a bullet from profile.yaml, character for
    character. There is no step at which it could become anything else.

    This is what pays for the design. "Proficient in React" required the model to
    WRITE a sentence. It no longer writes sentences — outside the summary — so it
    cannot write that one.
    """

    def test_a_bullet_is_copied_verbatim(self):
        selection = {"summary": "x",
                     "experience": [{"job": 1, "bullets": [0]}]}

        resume = resolve(selection, PROFILE)
        otrack = next(e for e in resume["experience"] if e["company"] == "Otrack")

        assert otrack["bullets"] == ["Built a Flutter app with API-driven data."]

    def test_the_model_chooses_which_bullets_and_in_what_order(self):
        """This is a real choice and a useful one — which two of a job's four
        bullets this employer should read first."""
        selection = {"summary": "x",
                     "experience": [{"job": 0, "bullets": [2, 0]}]}

        resume = resolve(selection, PROFILE)
        ta = next(e for e in resume["experience"]
                  if e["company"] == "Concordia University")

        assert ta["bullets"] == [
            "Reviewed student code and guided debugging.",
            "Supported students in Java and OOP.",
        ]

    def test_a_bullet_index_that_does_not_exist_is_dropped(self):
        selection = {"summary": "x",
                     "experience": [{"job": 2, "bullets": [0, 9]}]}

        resume = resolve(selection, PROFILE)
        bank = next(e for e in resume["experience"]
                    if e["company"] == "Bank Alfalah")

        assert bank["bullets"] == [
            "Troubleshot banking applications using logs and SQL."]

    def test_a_job_with_no_bullets_chosen_gets_its_first_few(self):
        """A resume with an employer and no bullets under it is worse than one with
        the wrong bullets. If the model says nothing, the profile decides."""
        selection = {"summary": "x", "experience": [{"job": 0}]}

        resume = resolve(selection, PROFILE)
        ta = resume["experience"][0]

        assert ta["bullets"][0] == "Supported students in Java and OOP."
        assert len(ta["bullets"]) >= 1


class TestProjectsAreAGenuineSubset:
    """Projects are the one place a real subset is chosen: nine in the profile,
    three on the page. That IS the model's judgement, and it is worth having."""

    def test_the_model_picks_which_three(self):
        selection = {"summary": "x",
                     "projects": [{"project": 2, "bullets": [0]},
                                  {"project": 0, "bullets": [0, 1]}]}

        resume = resolve(selection, PROFILE)

        assert [p["name"] for p in resume["projects"]] == \
            ["Risk Game", "Plant Disease Detection"]

    def test_it_cannot_pick_more_than_the_page_holds(self):
        selection = {"summary": "x",
                     "projects": [{"project": i, "bullets": [0]}
                                  for i in range(4)]}

        resume = resolve(selection, PROFILE)

        from src.paths import RESUME_PROJECTS_USED
        assert len(resume["projects"]) <= RESUME_PROJECTS_USED

    def test_the_same_project_twice_is_once(self):
        selection = {"summary": "x",
                     "projects": [{"project": 1, "bullets": [0]},
                                  {"project": 1, "bullets": [0]}]}

        resume = resolve(selection, PROFILE)

        assert [p["name"] for p in resume["projects"]] == ["Recipedia"]


class TestDatesAndFieldsComeFromTheProfile:
    def test_dates_are_formatted_from_the_profile(self):
        resume = resolve({"summary": "x"}, PROFILE)
        ta = resume["experience"][0]

        assert ta["dates"] == "Jan 2026 - Apr 2026"

    def test_the_location_is_the_profiles(self):
        resume = resolve({"summary": "x"}, PROFILE)

        assert resume["experience"][0]["location"] == "Montreal, QC"

    def test_the_volunteer_role_survives_for_the_renderer_to_fold_in(self):
        resume = resolve({"summary": "x"}, PROFILE)

        assert resume["volunteer"][0]["role"] == "STEM Co-Lead"


class TestWhatTheModelIsShown:
    def test_every_job_is_numbered(self):
        text = resume_select.choices(PROFILE)

        assert "[0] Teaching Assistant at Concordia University" in text
        assert "[2] Support Officer at Bank Alfalah" in text

    def test_every_bullet_is_numbered_under_its_job(self):
        """It cannot choose a bullet it was not shown."""
        text = resume_select.choices(PROFILE)

        assert "(0) Supported students in Java and OOP." in text
        assert "(3) Adapted explanations to different learning styles." in text

    def test_the_shape_asks_for_integers(self):
        shape = resume_select.shape_for_prompt(PROFILE)

        assert "integers" in shape
        assert "0..2" in shape          # three jobs

    def test_it_is_told_that_everything_appears(self):
        """The model does not choose whether a job appears — only where."""
        text = resume_select.choices(PROFILE)

        assert "every one of these appears on the resume" in text.lower() or \
               "every one of these appears" in text


class TestParsing:
    def test_a_code_fence(self):
        selection = {"summary": "Developer.", "skills": [0]}
        assert parse(f"```json\n{json.dumps(selection)}\n```")["summary"] == \
            "Developer."

    def test_a_sentence_in_front(self):
        selection = {"summary": "Developer."}
        assert parse(f"Here you go:\n{json.dumps(selection)}")["summary"] == \
            "Developer."

    def test_prose_instead_of_json(self):
        with pytest.raises(MalformedResume):
            parse("I'm sorry, I can't help with that.")
