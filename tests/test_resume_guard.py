"""The guards against a fabricated resume.

This file exists because of a real one. Asked to tailor a resume to a
PointClickCare sales posting, the model produced a complete, fluent, professional
resume for a person who does not exist: a sales manager with a Business
Administration degree from the University of Toronto and four years at
PointClickCare. The name at the top was "Canada Sales - Talent Community" — the
job title.

It was not disobeying its instructions. The profile was thin, so the fact sheet it
was handed was nearly empty, and it was given a template full of {{EXPERIENCE}}
placeholders, an instruction to leave no placeholder behind, and a full job
description. One source of facts remained. It used it.

No prompt fixes that. "Never invent an employer" is a rule with nothing behind it
when there is nothing else to write from. So there are two guards, and the
fabricated resume itself is the fixture they are tested against.
"""
import pytest

from src import resume_guard
from src.resume_guard import (
    FabricationError, ProfileIncompleteError, check_grounding, validate_profile,
)


#: The resume that was actually produced. Every line of it is false.
FABRICATED = """# Canada Sales - Talent Community

Mississauga, ON | example@email.com | LinkedIn | GitHub

## Summary

Dynamic sales professional with extensive experience in healthcare and SaaS.

## Education

### Bachelor of Business Administration @@ Sep 2015 - May 2019
University of Toronto, Toronto, ON

## Work Experience

### Canada Sales - Talent Community @@ June 2023 - Present
PointClickCare
- Drive sales initiatives in healthcare, SaaS, and enterprise software.

### Sales Manager @@ Jan 2021 - May 2023
Tech Solutions Inc., Toronto, ON
- Led a team of sales representatives in channel sales.

## Projects

### AI Integration Project - Personal (Python, FastAPI) @@ github.com/example/repo
- Developed an AI-driven sales forecasting tool.

## Certificates and Achievements

- Salesforce Administrator Certification @@ credly.com/badges/xyz

## Volunteer and Community Involvement

### Healthcare Advocacy Group - Volunteer
Organized fundraising events.
"""


#: The real person.
REAL_PROFILE = {
    "identity": {"name": "Safin Mahesania"},
    "summary": "MSc CS student.",
    # Enough skills that a Python/REST job clears the fit check — otherwise these
    # tests would be refused before a model is ever called, and they exist to
    # exercise what happens after it is.
    "skills": {"expert": ["Python", "REST APIs", "PostgreSQL", "Git"]},
    "skill_categories": [{"label": "Programming",
                          "skills": ["Python", "REST APIs", "PostgreSQL"]}],
    "experience": [{"role": "Software Developer Intern", "company": "Acme Corp"}],
    "education": [{"degree": "MSc", "institution": "Concordia University"}],
    "projects": [{"name": "JobPilot"}, {"name": "SafeRoute"}],
    "certificates": [{"name": "AWS Certified Cloud Practitioner"}],
    "volunteer": [{"organization": "Concordia Robotics Club"}],
}


HONEST = """# Safin Mahesania

## Summary

MSc Computer Science student at Concordia.

## Education

### Master of Science, Computer Science @@ Sep 2024 - Apr 2026
Concordia University, Montreal, QC

## Work Experience

### Software Developer Intern @@ May 2024 - Aug 2024
Acme Corp, Toronto, ON
- Cut API latency 40%.

## Projects

### JobPilot - Personal (Python, FastAPI) @@ github.com/x/job-pilot
- Built a pipeline.

## Certificates and Achievements

- AWS Certified Cloud Practitioner @@ credly.com/x

## Volunteer and Community Involvement

### Concordia Robotics Club / Mentor
Ran weekly sessions.
"""


class TestTheRealFabrication:
    """The regression. If any of these stops failing, the bug is back."""

    def test_the_invented_name_is_caught(self):
        """The tell. When the model invents a person, the name it picks is
        usually the job title, verbatim."""
        problems = check_grounding(FABRICATED, REAL_PROFILE)

        assert any("Canada Sales - Talent Community" in p and "not yours" in p
                   for p in problems)

    def test_the_invented_employers_are_caught(self):
        problems = check_grounding(FABRICATED, REAL_PROFILE)

        assert any("PointClickCare" in p for p in problems)
        assert any("Tech Solutions" in p for p in problems)

    def test_the_invented_degree_is_caught(self):
        problems = check_grounding(FABRICATED, REAL_PROFILE)
        assert any("University of Toronto" in p for p in problems)

    def test_the_invented_project_and_certificate_are_caught(self):
        problems = check_grounding(FABRICATED, REAL_PROFILE)

        assert any("AI Integration Project" in p for p in problems)
        assert any("Salesforce" in p for p in problems)

    def test_every_single_fabrication_is_caught(self):
        """Seven inventions. All seven."""
        problems = check_grounding(FABRICATED, REAL_PROFILE)
        assert len(problems) >= 7


class TestAnHonestResumePasses:
    """The guard is worthless if it also rejects the truth."""

    def test_a_real_resume_has_no_problems(self):
        assert check_grounding(HONEST, REAL_PROFILE) == []

    def test_a_company_with_a_location_appended_still_matches(self):
        """The profile says "Acme Corp"; the resume says "Acme Corp, Toronto, ON"."""
        assert check_grounding(HONEST, REAL_PROFILE) == []

    def test_punctuation_differences_are_tolerated(self):
        """"Acme Corp." and "Acme Corp" are the same employer."""
        profile = dict(REAL_PROFILE,
                       experience=[{"company": "Acme Corp."}])
        assert check_grounding(HONEST, profile) == []

    def test_a_redaction_placeholder_is_not_a_fabricated_name(self):
        """In redacted mode the name is {{NAME}} until it is substituted locally.
        That is not the model inventing a person."""
        redacted = HONEST.replace("# Safin Mahesania", "# {{NAME}}")

        problems = check_grounding(redacted, REAL_PROFILE)

        assert not any("name" in p.lower() for p in problems)


class TestProfileValidation:
    """Guard 1 — refuse before generating, rather than clean up afterwards."""

    def test_a_complete_profile_passes(self):
        assert validate_profile(REAL_PROFILE) == []

    def test_an_empty_profile_is_refused(self):
        missing = validate_profile({})
        assert len(missing) >= 4

    @pytest.mark.parametrize("drop,expected", [
        ("summary", "summary"),
        ("education", "education"),
        ("skill_categories", "skill_categories"),
    ])
    def test_each_missing_piece_is_named(self, drop, expected):
        profile = {k: v for k, v in REAL_PROFILE.items() if k != drop}
        if drop == "skill_categories":
            profile.pop("skills", None)

        missing = validate_profile(profile)

        assert any(expected in m for m in missing)

    def test_no_experience_and_no_projects_is_refused(self):
        """The one that caused this. With neither, there is nothing to write a
        resume about — and the model will find something."""
        profile = dict(REAL_PROFILE, experience=[], projects=[])

        missing = validate_profile(profile)

        assert any("experience or projects" in m for m in missing)

    def test_projects_alone_are_enough(self):
        """A student with no jobs yet still has a resume."""
        profile = dict(REAL_PROFILE, experience=[])
        assert validate_profile(profile) == []

    def test_experience_alone_is_enough(self):
        """And a career changer with no side projects."""
        profile = dict(REAL_PROFILE, projects=[])
        assert validate_profile(profile) == []


class TestGenerationRefuses:
    """The guards have to actually stop the document, not just note a concern."""

    def test_an_incomplete_profile_generates_nothing(self, conn, monkeypatch,
                                                     capture_llm):
        from src import apply
        monkeypatch.setattr(apply, "load_profile", lambda: {"identity": {}})

        with pytest.raises(ProfileIncompleteError):
            apply.generate_resume({"title": "Sales", "company": "PointClickCare",
                                   "description": "Sell things."})

        assert capture_llm.calls == [], (
            "a model was called for a profile that cannot support a resume"
        )

    def test_a_fabricated_resume_is_never_returned(self, conn, monkeypatch,
                                                   capture_llm):
        """Not a warning, not a draft to tidy up. It does not reach you."""
        from src import apply
        monkeypatch.setattr(apply, "load_profile", lambda: REAL_PROFILE)
        monkeypatch.setattr(apply, "extract_requirements",
                            lambda job: ["Python", "REST APIs"])
        monkeypatch.setattr(apply, "select_relevant_projects",
                            lambda job, **kw: [0])

        # A model that keeps inventing, even after being told not to.
        capture_llm.reply = lambda system, user: (FABRICATED, "gemini")

        # A job that FITS. The sales job is now refused before a model is called
        # at all — see test_resume_fit.py — so it cannot be used to exercise the
        # fabrication guard. This one gets through, and then the model lies.
        with pytest.raises(FabricationError) as caught:
            apply.generate_resume({"title": "Backend Developer", "company": "Shopify",
                                   "description": "Python, REST APIs, PostgreSQL."})

        assert any("PointClickCare" in p for p in caught.value.problems)

    def test_it_retries_once_before_refusing(self, conn, monkeypatch,
                                             capture_llm):
        """A single bad draft is not proof the model can't do it. Tell it exactly
        what it invented and let it try again."""
        from src import apply
        monkeypatch.setattr(apply, "load_profile", lambda: REAL_PROFILE)
        monkeypatch.setattr(apply, "extract_requirements", lambda job: ["Python"])
        monkeypatch.setattr(apply, "select_relevant_projects",
                            lambda job, **kw: [0])

        attempts = []

        def reply(system, user):
            attempts.append("retry" if "INVENTED FACTS" in user else "first")
            return (HONEST if "INVENTED FACTS" in user else FABRICATED, "gemini")

        capture_llm.reply = reply

        result = apply.generate_resume({"title": "Backend Dev", "company": "Shopify",
                                        "description": "Python."})

        assert attempts == ["first", "retry"]
        assert "Safin Mahesania" in result["text"]
        assert "PointClickCare" not in result["text"]


class TestAStringWhereAListBelongs:
    """The bug that turned nine projects into several thousand bullets.

    YAML will give you a string where you meant a list, and Python will iterate it
    — one character at a time. A profile with

        highlights: Plant disease detection using MobileNetV3.

    instead of

        highlights:
          - Plant disease detection using MobileNetV3.

    produced a prompt full of "- P", "- l", "- a", "- n", "- t". Nothing failed.
    Nothing warned. The model was handed noise where the projects should have been,
    and the resume it wrote was correspondingly untethered from reality.

    Six places iterated these fields — the resume writer, the cover letter, the
    scorer, the autofill. Fixing six call sites would have left the seventh. It is
    fixed once, at load.
    """

    def test_a_string_highlight_becomes_one_bullet_not_many_letters(self):
        from src.config import normalise_profile

        profile, _ = normalise_profile({"projects": [{
            "name": "Plant Disease Detection",
            "highlights": "Detection using MobileNetV3, 99.8% accuracy.",
        }]})

        highlights = profile["projects"][0]["highlights"]

        assert highlights == ["Detection using MobileNetV3, 99.8% accuracy."]
        assert len(highlights) == 1, "the string was iterated character by character"

    def test_the_prompt_no_longer_contains_single_letter_bullets(self):
        from src import apply
        from src.config import normalise_profile

        profile, _ = normalise_profile({"projects": [{
            "name": "Plant Disease Detection",
            "highlights": "Detection using MobileNetV3.",
        }]})

        rendered = apply._format_projects(profile["projects"])

        assert "- Detection using MobileNetV3." in rendered
        assert "\n    - P\n" not in rendered

    def test_experience_highlights_too(self):
        from src.config import normalise_profile

        profile, _ = normalise_profile({"experience": [{
            "role": "Flutter Developer",
            "highlights": "Built and shipped a mobile app.",
        }]})

        assert profile["experience"][0]["highlights"] == \
            ["Built and shipped a mobile app."]

    def test_a_string_tech_list_too(self):
        from src.config import normalise_profile

        profile, _ = normalise_profile({"projects": [
            {"name": "X", "tech": "Python"},
        ]})

        assert profile["projects"][0]["tech"] == ["Python"]

    def test_you_are_told_your_yaml_is_wrong(self):
        """Silently accepting a malformed profile means you never learn it is
        malformed — and the next field you get wrong fails just as quietly."""
        from src.config import normalise_profile

        _, warnings = normalise_profile({"projects": [{
            "name": "Plant Disease Detection",
            "highlights": "One long string.",
        }]})

        assert any("highlights" in w and "should be a YAML list" in w
                   for w in warnings)

    def test_a_correct_profile_produces_no_warnings(self):
        from src.config import normalise_profile

        _, warnings = normalise_profile({"projects": [{
            "name": "X", "tech": ["Python"], "highlights": ["A point."],
        }]})

        assert warnings == []

    def test_a_correct_profile_is_left_alone(self):
        from src.config import normalise_profile

        profile, _ = normalise_profile({"projects": [{
            "name": "X", "highlights": ["First point.", "Second point."],
        }]})

        assert profile["projects"][0]["highlights"] == ["First point.",
                                                        "Second point."]


class TestPlaceholdersAreRefused:
    """A TODO left in the profile is worse than a missing field.

    A missing field is simply absent from the resume. A placeholder is handed to
    the model as though it were a fact — and the model will either print
    "TODO — a third point" on your resume, or, worse, read the gap it describes and
    fill it. Inventing content to satisfy a TODO is precisely the failure this
    module exists to prevent, arriving through the front door.
    """

    def _profile(self, **overrides):
        base = {
            "identity": {"name": "A Name"},
            "summary": "A real summary.",
            "skill_categories": [{"label": "X", "skills": ["Y"]}],
            "education": [{"degree": "MSc"}],
            "projects": [{"name": "P", "highlights": ["A real point."]}],
        }
        base.update(overrides)
        return base

    def test_a_todo_in_a_project_refuses_generation(self):
        profile = self._profile(projects=[{
            "name": "Plant Disease Detection",
            "highlights": ["A real point.", "TODO — a third point."],
        }])

        problems = validate_profile(profile)

        assert any("placeholder" in p for p in problems)

    def test_the_offending_point_is_named(self):
        """"Something is wrong with your profile" is not actionable. The project
        and the point number are."""
        profile = self._profile(projects=[{
            "name": "Risk Game",
            "highlights": ["Real.", "Real.", "TODO — write this."],
        }])

        problems = validate_profile(profile)

        assert any("Risk Game" in p and "point 3" in p for p in problems)

    def test_lorem_ipsum_and_friends_are_caught(self):
        for junk in ["TBD", "FIXME: write this", "Lorem ipsum dolor sit amet",
                     "Notable outcome or scale."]:
            profile = self._profile(projects=[{
                "name": "P", "highlights": [junk],
            }])
            assert validate_profile(profile), f"{junk!r} slipped through"

    def test_the_example_profiles_own_placeholders_are_caught(self):
        """profile.example.yaml ships with "your first point" in it. Copying the
        example and generating from it unedited must not be possible."""
        profile = self._profile(projects=[{
            "name": "Example Project",
            "highlights": ["your first point", "your second point"],
        }])

        assert validate_profile(profile)

    def test_a_placeholder_in_the_summary_is_caught(self):
        assert validate_profile(self._profile(summary="TODO — write this."))

    def test_a_real_profile_passes(self):
        profile = self._profile(projects=[{
            "name": "Plant Disease Detection",
            "highlights": [
                "Built a classifier on MobileNetV3-Small with transfer learning.",
                "Designed the augmentation pipeline in PyTorch, 99.8% accuracy.",
                "Deployed it as a Flutter app for offline use.",
            ],
        }])

        assert validate_profile(profile) == []

    def test_a_legitimate_word_containing_a_placeholder_is_not_flagged(self):
        """"Extended the toolkit" must not trip on "todo" hiding inside a word."""
        profile = self._profile(projects=[{
            "name": "P",
            "highlights": ["Built a photodocumentation tool for field surveys."],
        }])

        assert validate_profile(profile) == []


class TestTheListsAreClosed:
    """"Never invent an employer" did not stop the model inventing an employer.

    It added "TechCorp Inc., Karachi, Pakistan" to a profile that already had three
    real jobs. The guard caught it and the resume was refused — which is the system
    working, but a tool that refuses every time is not a tool.

    The prohibition was the problem. It says what not to do without ever saying
    what the complete truth IS, so the model never learns the list is finished, and
    a job asking for more experience than you have is a gap it will quietly fill.

    Counting closes the world. "You have exactly three employers, here they are,
    do not add a 4th" turns an open generative task into a fill-in-the-blanks one.
    """

    PROFILE = {
        "experience": [{"company": "Concordia University"},
                       {"company": "Otrack"},
                       {"company": "Bank Alfalah"}],
        "education": [{"institution": "Concordia University"}],
        "certificates": [{"name": "Azure Fundamentals"}],
        "volunteer": [],
    }

    def test_the_employers_are_counted_and_named(self):
        from src.apply import closed_lists

        text = closed_lists(self.PROFILE)

        assert "EXACTLY 3" in text
        assert "1. Concordia University" in text
        assert "2. Otrack" in text
        assert "3. Bank Alfalah" in text
        assert "Do not add a 4th" in text

    def test_an_empty_section_is_stated_as_empty(self):
        """Not merely omitted. An absent section is a gap the model may fill; a
        section explicitly declared empty is a fact."""
        from src.apply import closed_lists

        text = closed_lists(self.PROFILE)

        assert "VOLUNTEER ORGANISATIONS: you have NONE" in text
        assert "must not contain a Volunteer" in text

    def test_the_ordinals_are_not_embarrassing(self):
        from src.apply import _ordinal

        assert _ordinal(2) == "a 2nd"
        assert _ordinal(3) == "a 3rd"
        assert _ordinal(4) == "a 4th"
        assert _ordinal(11) == "a 11th"      # not "a 11st"

    def test_the_closed_list_is_in_the_prompt(self, conn, monkeypatch,
                                              capture_llm):
        from src import apply

        # REAL_PROFILE, with three employers so the count is worth asserting.
        profile = dict(REAL_PROFILE, experience=[
            {"role": "Software Developer Intern", "company": "Acme Corp"},
            {"role": "Flutter Developer", "company": "Otrack"},
            {"role": "Support Officer", "company": "Bank Alfalah"},
        ])
        monkeypatch.setattr(apply, "load_profile", lambda: profile)
        monkeypatch.setattr(apply, "extract_requirements", lambda job: ["Python"])
        monkeypatch.setattr(apply, "select_relevant_projects", lambda job, **kw: [0])
        capture_llm.reply = lambda system, user: (HONEST, "gemini")

        apply.generate_resume({"title": "Dev", "company": "Shopify",
                               "description": "Python."})

        prompt = "\n".join(user for _, user, _ in capture_llm.calls)

        assert "EXACTLY 3" in prompt
        assert "Do not add a 4th" in prompt
        assert "Everything below is ME. Everything above is the job." in prompt

    def test_the_retry_carries_the_closed_list_too(self, conn, monkeypatch,
                                                   capture_llm):
        """The model already ignored the open-ended version once. Repeating it
        verbatim and hoping is not a strategy."""
        from src import apply

        profile = dict(REAL_PROFILE, experience=[
            {"role": "Software Developer Intern", "company": "Acme Corp"},
            {"role": "Flutter Developer", "company": "Otrack"},
            {"role": "Support Officer", "company": "Bank Alfalah"},
        ])
        monkeypatch.setattr(apply, "load_profile", lambda: profile)
        monkeypatch.setattr(apply, "extract_requirements", lambda job: ["Python"])
        monkeypatch.setattr(apply, "select_relevant_projects", lambda job, **kw: [0])

        def reply(system, user):
            return (HONEST if "INVENTED FACTS" in user else FABRICATED, "gemini")

        capture_llm.reply = reply

        apply.generate_resume({"title": "Dev", "company": "Shopify",
                               "description": "Python."})

        retry = next(user for _, user, _ in capture_llm.calls
                     if "INVENTED FACTS" in user)

        assert "EXACTLY 3" in retry
        assert "Do not add a 4th" in retry


class TestTheJobIsNotWhoYouAre:
    """A second, worse fabrication — one that wore different clothes.

    Given a PointClickCare *sales* posting, the model produced a resume whose
    summary began "Dynamic sales professional" and whose skills read "Sales
    Experience: Healthcare, SaaS". No employer was invented, so the original guard
    passed it. But the skills and the summary had been lifted wholesale from the
    job description for a computer science student who has never worked in sales.

    An invented skill category is an invented fact. It just isn't a proper noun.

    And when told it had invented an employer, the model's correction was to delete
    the entire Work Experience section — "(No work experience listed)", for someone
    with three real jobs. Over-correction is not a fix. A resume that omits your
    career is as false as one that invents one.
    """

    PROFILE = {
        "identity": {"name": "Safin Mahesania"},
        "skill_categories": [
            {"label": "Programming & Markup Languages", "skills": ["Dart", "Python"]},
            {"label": "Databases", "skills": ["MySQL"]},
        ],
        "experience": [{"company": "Concordia University"},
                       {"company": "Otrack"},
                       {"company": "Bank Alfalah"}],
        "education": [{"institution": "Concordia University"}],
        "projects": [{"name": "Plant Disease Detection"}],
    }

    SALES_RESUME = """# Safin Mahesania

## Summary

Dynamic sales professional with a background in healthcare, SaaS and enterprise software.

## Skills

- **Sales Experience:** Healthcare | SaaS | Enterprise Software
- **Inside Sales Expertise:** Channel Sales | Account Management

## Education

### Master of Science in Computer Science @@ Jan 2025 - Aug 2026
Concordia University, Montreal, Canada

## Work Experience

(No work experience listed)

## Projects

### Plant Disease Detection @@ github.com/x
- Built a classifier.
"""

    def test_a_skill_category_from_the_job_is_caught(self):
        problems = check_grounding(self.SALES_RESUME, self.PROFILE)

        assert any("Sales Experience" in p and "not a skill category" in p
                   for p in problems)

    def test_deleting_your_real_work_history_is_caught(self):
        """The model's idea of a correction."""
        problems = check_grounding(self.SALES_RESUME, self.PROFILE)

        assert any("Work Experience section is empty" in p for p in problems)

    def test_your_own_skill_categories_pass(self):
        honest = """# Safin Mahesania

## Skills

- **Programming & Markup Languages:** Dart | Python
- **Databases:** MySQL

## Education

### MSc @@ 2025
Concordia University

## Work Experience

### TA @@ 2026
Concordia University
- Taught Java.

## Projects

### Plant Disease Detection @@ github.com/x
- Built a classifier.
"""
        assert check_grounding(honest, self.PROFILE) == []

    def test_the_retry_forbids_deleting_sections(self, conn, monkeypatch,
                                                 capture_llm):
        from src import apply

        profile = dict(REAL_PROFILE)
        monkeypatch.setattr(apply, "load_profile", lambda: profile)
        monkeypatch.setattr(apply, "extract_requirements",
                            lambda job: ["Python", "REST APIs"])
        monkeypatch.setattr(apply, "select_relevant_projects", lambda job, **kw: [0])
        capture_llm.reply = lambda system, user: (
            HONEST if "INVENTED FACTS" in user else FABRICATED, "gemini")

        apply.generate_resume({"title": "Backend Developer", "company": "Shopify",
                               "description": "Python, REST APIs, PostgreSQL."})

        retry = next(user for _, user, _ in capture_llm.calls
                     if "INVENTED FACTS" in user)

        assert "Do NOT delete a section" in retry
        assert "every real entry present, every invented one gone" in retry


class TestTheProjectNotesDoNotLeak:
    """"Plant Disease Detection (owner: course) - Personal (PyTorch...)" appeared on
    a real resume. "(owner: course)" is an internal annotation from the prompt; the
    model copied it verbatim, because it was handed a line that looked like output.

    Notes must look like notes.
    """

    def test_the_owner_annotation_is_not_inline(self):
        from src import apply

        rendered = apply._format_projects([{
            "name": "Plant Disease Detection", "owner": "course",
            "tech": ["PyTorch"], "link": "github.com/x",
            "description": "AI Project", "highlights": ["Built it."],
        }])

        assert "(owner: course)" not in rendered
        assert "OWNER: course" in rendered

    def test_each_field_is_labelled_on_its_own_line(self):
        from src import apply

        rendered = apply._format_projects([{
            "name": "P", "tech": ["Python"], "link": "github.com/x",
            "highlights": ["Did a thing."],
        }])

        assert "\n    TECH: Python" in rendered
        assert "\n    LINK: github.com/x" in rendered
