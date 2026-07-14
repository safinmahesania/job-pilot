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



class TestTechnologiesNamedInProse:
    """The invention moved into the prose, because that was the only place left.

    The structured checks cover everything that lives in a FIELD — employers,
    schools, projects, certificates, skill category labels. The summary is free text
    and nothing was reading it, and so:

        "Proficient in React, C# .NET, and full-stack feature delivery, with
        hands-on experience integrating REST APIs, SQL/NoSQL databases..."

    React is not in the profile. It is in the job description. The resume claimed a
    framework its owner has never touched — which is exactly an invented employer,
    wearing prose instead of a field, and getting through for exactly the same
    reason: nothing was checking.
    """

    PROFILE = {
        "identity": {"name": "Safin"},
        "summary": "Software developer across frontend and backend.",
        "skills": {
            "expert": ["Java", "Flutter", "Python", "SQL", "RESTful APIs"],
            "proficient": ["Dart", "C#", "Firebase", "SQLite"],
            "familiar": ["Azure Cloud", ".NET 8", "PyTorch"],
        },
        "skill_categories": [{"label": "Databases",
                              "skills": ["MySQL", "SQL Server"]}],
        "experience": [{"role": "Flutter Developer", "company": "Otrack",
                        "highlights": ["Built a Flutter app with Firebase."]}],
        "projects": [{"name": "Recipedia", "tech": ["Flutter", "Dart"]}],
        "certificates": [],
    }

    THE_SUMMARY = (
        "Software developer experienced across frontend and backend development, "
        "adept at building clean, practical, and maintainable applications. "
        "Proficient in React, C# .NET, and full-stack feature delivery, with "
        "hands-on experience integrating REST APIs, SQL/NoSQL databases, and cloud "
        "platforms like Azure. Driven to shape architecture, engineering practices, "
        "and deliver scalable solutions."
    )

    def test_react_is_caught(self):
        """The one that went out."""
        problems = resume_guard.check_prose({"summary": self.THE_SUMMARY},
                                            self.PROFILE)

        assert any('"React"' in p for p in problems)

    def test_nosql_is_caught_separately_from_sql(self):
        """"SQL/NoSQL" is two claims, and only one of them is a lie."""
        problems = resume_guard.check_prose({"summary": self.THE_SUMMARY},
                                            self.PROFILE)

        assert any('"NoSQL"' in p for p in problems)
        assert not any('"SQL"' in p for p in problems)

    def test_it_says_where_the_word_came_from(self):
        problems = resume_guard.check_prose({"summary": self.THE_SUMMARY},
                                            self.PROFILE)

        assert any("came from the job posting" in p for p in problems)

    def test_the_technologies_you_do_have_pass(self):
        """C#, .NET, REST, Azure — all real, all differently spelled from the
        profile, all fine. "REST" lives inside "RESTful APIs"; ".NET" inside
        ".NET 8"."""
        honest = ("Junior software developer across frontend and backend, with "
                  "hands-on work in Flutter, Java and Python. Experience "
                  "integrating REST APIs and SQL databases, with exposure to "
                  "Azure and .NET 8.")

        assert resume_guard.check_prose({"summary": honest}, self.PROFILE) == []

    def test_ordinary_prose_claims_nothing(self):
        """"Curious", "collaborative", "problem-solving" — words, not tools. A check
        that fired on these would be useless."""
        prose = ("Curious and collaborative developer with a strong problem-solving "
                 "mindset and a drive to keep learning and building scalable "
                 "solutions.")

        assert resume_guard.check_prose({"summary": prose}, self.PROFILE) == []

    def test_a_sentence_opening_with_a_capital_is_not_a_claim(self):
        """"Software developer..." starts with a capital because sentences do."""
        assert resume_guard.check_prose(
            {"summary": "Software engineer. Driven to build well."},
            self.PROFILE) == []

    def test_stacks_you_have_never_touched(self):
        problems = resume_guard.check_prose(
            {"summary": "Developer skilled in Kubernetes, Terraform and Angular."},
            self.PROFILE)

        for tool in ("Kubernetes", "Terraform", "Angular"):
            assert any(tool in p for p in problems)

    def test_experience_bullets_are_checked_too(self):
        """The summary is not the only prose on the page."""
        resume = {"summary": "", "experience": [{
            "company": "Otrack",
            "bullets": ["Rebuilt the front end in React and deployed to Kubernetes."],
        }]}

        problems = resume_guard.check_prose(resume, self.PROFILE)

        assert any("React" in p and "Otrack" in p for p in problems)

    def test_project_bullets_are_checked_too(self):
        resume = {"summary": "", "projects": [{
            "name": "Recipedia",
            "bullets": ["Built the client in Angular."],
        }]}

        problems = resume_guard.check_prose(resume, self.PROFILE)

        assert any("Angular" in p and "Recipedia" in p for p in problems)

    def test_generation_refuses_a_resume_claiming_a_framework_you_lack(
            self, conn, monkeypatch, capture_llm):
        import json

        from src import apply

        profile = dict(self.PROFILE, contact={"city": "Montreal"},
                       education=[{"degree": "MSc",
                                   "institution": "Concordia University"}],
                       certificates=[], volunteer=[])
        monkeypatch.setattr(apply, "load_profile", lambda: profile)
        monkeypatch.setattr(apply, "redacting", lambda: False)
        monkeypatch.setattr(apply, "extract_requirements",
                            lambda job: ["React", "C#", "SQL"])
        monkeypatch.setattr(apply, "select_relevant_projects",
                            lambda job, **kw: [0])

        lying = json.dumps({
            "summary": self.THE_SUMMARY,
            "skills": ["Databases"],
            "experience": [{"role": "Flutter Developer", "company": "Otrack",
                            "location": "", "dates": "2024",
                            "bullets": ["Built an app."]}],
            "education": [{"degree": "MSc", "institution": "Concordia University",
                           "location": "", "dates": "2026"}],
            "projects": [{"name": "Recipedia", "link": "",
                          "bullets": ["An app."]}],
            "certificates": [], "volunteer": [],
        })
        capture_llm.reply = lambda system, user: (lying, "gemini")

        with pytest.raises(FabricationError) as caught:
            apply.generate_resume({"title": "Full Stack", "company": "Acme",
                                   "description": "React, C#, SQL."})

        assert any("React" in p for p in caught.value.problems)

    def test_the_shape_tells_the_model_first(self):
        """The check is the backstop. The instruction is the plan."""
        from src import resume_schema

        assert "Name ONLY technologies that appear in MY PROFILE" in \
            resume_schema.SHAPE["summary"]
