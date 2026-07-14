"""The resume as data, not as text the model has to format correctly.

The failure that forced this: a resume was refused with

    The Work Experience section is empty, but your profile has 3.
    The Education section is empty, but your profile has 2.

for a resume that contained three jobs and two degrees. The model had not invented
anything and had not dropped anything. It had written

    **Teaching Assistant** | Concordia University        Jan 2026 - Apr 2026

where the parser wanted

    ### Teaching Assistant @@ Jan 2026 - Apr 2026
    Concordia University

Identical to a reader. Invisible to a regex. The guard read the section as empty
and refused an honest document, and no amount of prompting would reliably fix it,
because the model was being asked to hit a formatting convention exactly, every
time, in a document with six sections and no feedback.

A verification that fails on formatting cannot tell you anything about truth.

So the model stopped writing documents. It fills in fields; the code renders the
page. It never sees a `###` or an `@@`, so it cannot get them wrong — and every
check downstream reads a structure that was given rather than one guessed back out
of prose.
"""
import json

import pytest

from src import resume_guard, resume_limits, resume_schema
from src.resume_schema import MalformedResume, parse, to_markdown


PROFILE = {
    "identity": {"name": "Safin Mahesania"},
    "contact": {"city": "Montreal", "province": "QC", "country": "Canada",
                "email": "s@example.com", "phone": "+1 514 555 0123",
                "linkedin": "linkedin.com/in/safinmahesania",
                "github": "github.com/safinmahesania"},
    "skill_categories": [{"label": "Databases", "skills": ["MySQL"]}],
    # Real skills, so that prose naming them is grounded. A profile with no "API"
    # anywhere in it cannot honestly have a bullet about API-driven data — and the
    # prose check is right to say so, which is what caught this fixture.
    "skills": {"expert": ["Flutter", "Java", "REST APIs", "SQL"]},
    "experience": [{"company": "Concordia University"},
                   {"company": "Otrack"},
                   {"company": "Bank Alfalah"}],
    "education": [{"institution": "Concordia University"},
                  {"institution": "SZABIST"}],
    "projects": [{"name": "Recipedia"}],
    "certificates": [{"name": "Azure Fundamentals"}],
    "volunteer": [],
}

HONEST = {
    "summary": "Software developer with Flutter and backend experience.",
    "skills": [{"label": "Databases", "skills": ["MySQL", "SQLite"]}],
    "experience": [
        {"role": "Flutter Developer", "company": "Otrack", "location": "Karachi, PK",
         "dates": "Jan 2023 - Jul 2024",
         "bullets": ["Built and shipped a Flutter app with API-driven data."]},
        {"role": "Teaching Assistant", "company": "Concordia University",
         "location": "Montreal, QC", "dates": "Jan 2026 - Apr 2026",
         "bullets": ["Supported students in Java and OOP."]},
        {"role": "Application Support Officer", "company": "Bank Alfalah",
         "location": "Karachi, PK", "dates": "2021 - 2022",
         "bullets": ["Troubleshot banking applications via logs and SQL."]},
    ],
    "education": [
        {"degree": "Master of Science, Computer Science",
         "institution": "Concordia University", "location": "Montreal, QC",
         "dates": "Jan 2025 - Aug 2026"},
        {"degree": "BSc Computer Science", "institution": "SZABIST",
         "location": "Karachi, PK", "dates": "2018 - 2022"},
    ],
    "projects": [
        {"name": "Recipedia", "owner": "course", "tech": ["Flutter", "Dart"],
         "link": "https://github.com/safinmahesania/Recipedia",
         "bullets": ["Cross-platform app that scans produce and suggests recipes."]},
    ],
    "certificates": [{"name": "Azure Fundamentals", "date": "2025",
                      "link": "https://learn.microsoft.com/x"}],
    "volunteer": [],
}


class TestTheFalseAlarmIsGone:
    """The bug that forced the rewrite."""

    BOLD_HEADINGS = """# Safin Mahesania

## Work Experience

**Teaching Assistant** | Concordia University   Jan 2026 - Apr 2026
- Supported students in Java.

**Flutter Developer** | Otrack   Jan 2023 - Jul 2024
- Built a Flutter app.

## Education

**Master of Science** | Concordia University   2025 - 2026
"""

    def test_the_markdown_parser_used_to_reject_an_honest_resume(self):
        """Kept as the record of what went wrong. This resume is entirely true —
        three real jobs, a real degree — and the old check calls it empty."""
        problems = resume_guard.check_grounding(self.BOLD_HEADINGS, PROFILE)

        assert any("Work Experience section is empty" in p for p in problems), (
            "this test documents a false positive; if it stops firing, the old "
            "parser was fixed and this file's premise needs revisiting"
        )


class TestParsingWhatTheModelReturns:
    def test_plain_json(self):
        assert parse(json.dumps(HONEST))["summary"] == HONEST["summary"]

    def test_json_in_a_code_fence(self):
        fenced = f"```json\n{json.dumps(HONEST)}\n```"
        assert parse(fenced)["summary"] == HONEST["summary"]

    def test_json_with_a_sentence_in_front_of_it(self):
        chatty = f"Here is the resume:\n\n{json.dumps(HONEST)}"
        assert parse(chatty)["summary"] == HONEST["summary"]

    def test_a_missing_list_becomes_an_empty_list_not_a_crash(self):
        resume = parse('{"summary": "Hi"}')

        assert resume["experience"] == []
        assert resume["skills"] == []

    def test_prose_instead_of_json_is_an_error_not_a_guess(self):
        with pytest.raises(MalformedResume):
            parse("I'm sorry, I can't help with that.")


class TestRendering:
    """The layout belongs entirely to the code now. The model never touches it."""

    def test_the_model_never_writes_a_heading(self):
        markdown = to_markdown(HONEST, PROFILE, "Safin Mahesania")

        assert "### Flutter Developer @@ Jan 2023 - Jul 2024" in markdown
        assert "## Work Experience" in markdown

    def test_every_real_entry_is_rendered(self):
        markdown = to_markdown(HONEST, PROFILE, "Safin Mahesania")

        for company in ("Otrack", "Concordia University", "Bank Alfalah"):
            assert company in markdown

    def test_an_empty_section_is_left_out_entirely(self):
        """The profile has no volunteer work. An empty heading is worse than none."""
        markdown = to_markdown(HONEST, PROFILE, "Safin Mahesania")

        assert "Volunteer" not in markdown

    def test_the_project_heading_is_the_name_alone(self):
        """It once read "Recipedia - Course - Course (Flutter, Dart, Firebase,
        Python, SQLite, Mobile Development)" and collided with the right-aligned
        link. The owner and the tech stack are notes for the model, not headings
        for a reader."""
        markdown = to_markdown(HONEST, PROFILE, "Safin Mahesania")

        assert "### Recipedia @@" in markdown
        assert "owner:" not in markdown
        assert "Course" not in markdown

    def test_links_go_to_the_right_of_the_at_marker(self):
        markdown = to_markdown(HONEST, PROFILE, "Safin Mahesania")

        assert "@@ https://github.com/safinmahesania/Recipedia" in markdown
        assert "@@ https://learn.microsoft.com/x" in markdown

    def test_redacted_mode_leaves_the_contact_placeholders(self):
        """One placeholder for the whole line, not one per field. The line is built
        by a single function either way — the only difference is when."""
        markdown = to_markdown(HONEST, PROFILE, "{{NAME}}", redacted=True)

        assert "{{NAME}}" in markdown
        assert "{{CONTACT}}" in markdown
        assert "s@example.com" not in markdown

    def test_the_real_contact_is_used_when_not_redacting(self):
        markdown = to_markdown(HONEST, PROFILE, "Safin Mahesania")

        assert "Montreal, QC, Canada" in markdown
        assert "linkedin.com/in/safinmahesania" in markdown

    def test_it_renders_through_the_docx_renderer(self):
        from src import materials

        markdown = to_markdown(HONEST, PROFILE, "Safin Mahesania")
        data = materials.to_docx(markdown, "resume")

        assert data[:2] == b"PK"


class TestLengthOnTheStructure:
    def test_a_long_summary_is_caught(self):
        long = "delivered " * 60
        overruns = resume_limits.check_structured(dict(HONEST, summary=long))

        assert any(o.where == "Summary" for o in overruns)

    def test_a_long_experience_bullet_names_the_company(self):
        """"Experience bullet 2" is less useful than "Experience bullet 2
        (Otrack)"."""
        long = "delivered " * 40
        resume = dict(HONEST)
        resume["experience"] = [dict(HONEST["experience"][0], bullets=[long])]

        overruns = resume_limits.check_structured(resume)

        assert any("Otrack" in o.where for o in overruns)

    def test_an_honest_resume_has_no_overruns(self):
        assert resume_limits.check_structured(HONEST) == []



class TestTheRenderBugsFromTheFirstRealResume:
    """Everything that was wrong with the first document that actually reached a
    human eye. Each of these is a line that appeared on a real page."""

    PROFILE = {
        "identity": {"name": "Safin Mahesania"},
        "contact": {"city": "Montreal", "province": "Quebec",
                    "email": "safin.mahesania@outlook.com",
                    "phone": "+1 437 661 5569",
                    "linkedin": "https://www.linkedin.com/in/safinmahesania/",
                    "github": "https://github.com/safinmahesania"},
        "skill_categories": [
            {"label": "Programming & Markup Languages",
             "skills": ["Dart", "Python", "Java"]},
            {"label": "Databases", "skills": ["MySQL", "SQLite"]},
        ],
        "experience": [], "education": [], "projects": [{"name": "Recipedia"}],
    }

    RESUME = {
        "summary": "Software developer.",
        "skills": ["Databases", "Programming & Markup Languages"],
        "experience": [], "education": [],
        "projects": [{"name": "Recipedia", "owner": "course",
                      "tech": ["Flutter", "Dart", "Firebase"],
                      "link": "https://github.com/safinmahesania/Recipedia",
                      "bullets": ["Scans produce and suggests recipes."]}],
        "certificates": [], "volunteer": [],
    }

    def _md(self):
        return to_markdown(self.RESUME, self.PROFILE, "Safin Mahesania")

    def test_the_project_heading_is_just_the_name(self):
        """It read "Recipedia - Course - Course (Flutter, Dart, Firebase, Python,
        SQLite, Mobile Development)" and ran straight into the right-aligned link,
        overlapping it. The technologies belong in the bullets, doing work."""
        markdown = self._md()

        assert "### Recipedia @@" in markdown
        assert "Course" not in markdown
        assert "(Flutter" not in markdown

    def test_the_skill_categories_are_not_prefixed_with_the_words_skill_category(self):
        """The fact sheet labels them "Skill category — Databases:" for the model's
        benefit. The model copied the label, annotation and all, onto the page —
        the same way it once copied "(owner: course)"."""
        markdown = self._md()

        assert "Skill category" not in markdown
        assert "- **Databases:** MySQL | SQLite" in markdown

    def test_the_skills_come_from_the_profile_not_the_model(self):
        """There was never a reason to ask. The categories are a fixed list; the
        model's only useful contribution is the order."""
        markdown = self._md()

        # Model asked for Databases first. It got Databases first — with the
        # profile's contents, not its own.
        assert markdown.index("Databases:") < markdown.index("Programming & Markup")
        assert "Dart | Python | Java" in markdown

    def test_a_category_the_model_forgot_is_still_rendered(self):
        """Dropping a skill category is dropping a fact."""
        resume = dict(self.RESUME, skills=["Databases"])

        markdown = to_markdown(resume, self.PROFILE, "Safin")

        assert "Programming & Markup Languages" in markdown

    def test_an_invented_category_cannot_appear_at_all(self):
        """It cannot be rendered, because the renderer only knows the profile's."""
        resume = dict(self.RESUME, skills=["Sales Experience", "Databases"])

        markdown = to_markdown(resume, self.PROFILE, "Safin")

        assert "Sales Experience" not in markdown

    def test_the_profile_urls_lose_their_scheme_and_www(self):
        """"https://www.linkedin.com/in/safinmahesania/" is a URL.
        "linkedin.com/in/safinmahesania" is a person."""
        markdown = self._md()

        assert "linkedin.com/in/safinmahesania" in markdown
        assert "github.com/safinmahesania" in markdown
        assert "https://" not in markdown.split("## Summary")[0]
        assert "www." not in markdown

    def test_contact_details_are_on_one_line(self):
        markdown = self._md()
        line = next(l for l in markdown.splitlines() if "outlook.com" in l)

        for part in ("+1 437 661 5569", "linkedin.com/in/", "github.com/"):
            assert part in line


class TestTheWordFile:
    """What Word actually renders. The markdown can be perfect and the page still
    wrong."""

    def _docx(self):
        import io
        import zipfile

        from src import materials

        markdown = TestTheRenderBugsFromTheFirstRealResume()._md()
        data = materials.to_docx(markdown, "resume")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return z.read("word/document.xml").decode()

    def test_the_font_is_calibri_throughout(self):
        import re
        xml = self._docx()

        assert set(re.findall(r'w:ascii="([^"]+)"', xml)) == {"Calibri"}

    def test_the_name_is_20pt_and_everything_else_is_11(self):
        import re
        xml = self._docx()

        sizes = sorted({int(s) // 2 for s in re.findall(r'<w:sz w:val="(\d+)"', xml)})

        assert sizes == [11, 20]

    def test_the_contact_line_is_not_justified(self):
        """Justifying a short line stretches the spaces inside it until
        "+1 437 661 5569" reads as four separate numbers."""
        import re
        xml = self._docx()

        contact = next(p for p in re.findall(r"<w:p>.*?</w:p>", xml, re.S)
                       if "outlook.com" in p)

        assert 'w:jc w:val="both"' not in contact

    def test_the_bullets_are_real_bullets(self):
        assert "ListBullet" in self._docx()



class TestTheContactLineIsBuiltInOnePlace:
    """The LinkedIn URL that vanished.

    The header was assembled twice: once in the renderer, which shortened the URLs
    and joined them with the pipe the renderer splits on, and once inside
    fill_contact(), which joined the raw URLs with a middle dot to fill a {{LINKS}}
    placeholder.

    Only the first one was any good. And only the second one ran, because redacted
    mode is the default — so the shortening never happened, the link labeller never
    saw a URL it recognised, and LinkedIn went out as part of a string the renderer
    could not read.

    Two builders for one line is one builder too many.
    """

    PROFILE = {
        "identity": {"name": "Safin Mahesania"},
        "contact": {"city": "Montreal", "province": "Quebec",
                    "email": "s@example.com", "phone": "+1 437 661 5569",
                    "linkedin": "https://www.linkedin.com/in/safinmahesania/",
                    "github": "https://github.com/safinmahesania"},
        "skill_categories": [], "experience": [], "education": [],
        "projects": [], "certificates": [], "volunteer": [],
    }

    EMPTY = {"summary": "", "skills": [], "experience": [], "education": [],
             "projects": [], "certificates": [], "volunteer": []}

    def test_linkedin_survives_redaction(self):
        """It did not. It was the whole bug."""
        from src.apply import fill_contact

        markdown = to_markdown(self.EMPTY, self.PROFILE, "{{NAME}}", redacted=True)
        filled = fill_contact(markdown, self.PROFILE)

        assert "linkedin.com/in/safinmahesania" in filled

    def test_redacted_and_plain_produce_the_same_header(self):
        """The only difference between the two paths should be WHEN the line is
        filled in, never WHAT it says."""
        from src.apply import fill_contact

        plain = to_markdown(self.EMPTY, self.PROFILE, "Safin Mahesania")
        redacted = fill_contact(
            to_markdown(self.EMPTY, self.PROFILE, "{{NAME}}", redacted=True),
            self.PROFILE)

        assert plain == redacted

    def test_the_urls_are_shortened_in_both(self):
        from src.apply import fill_contact

        filled = fill_contact(
            to_markdown(self.EMPTY, self.PROFILE, "{{NAME}}", redacted=True),
            self.PROFILE)

        assert "https://www." not in filled
        assert "linkedin.com/in/safinmahesania" in filled

    def test_they_are_pipe_separated_so_the_renderer_can_split_them(self):
        """The middle dot was invisible to a renderer that splits on "|"."""
        from src.apply import fill_contact

        filled = fill_contact(
            to_markdown(self.EMPTY, self.PROFILE, "{{NAME}}", redacted=True),
            self.PROFILE)
        line = next(l for l in filled.splitlines() if "s@example.com" in l)

        assert line.count("|") == 3
        assert "\u00b7" not in line

    def test_nothing_is_dropped_when_a_field_is_missing(self):
        """A missing website must not take LinkedIn down with it."""
        from src.resume_schema import contact_line

        profile = dict(self.PROFILE,
                       contact=dict(self.PROFILE["contact"], website=None))

        line = contact_line(profile)[1]

        assert "linkedin.com/in/" in line
        assert "github.com/" in line


class TestTheVolunteerSlash:
    """"Al-Azhar Garden Student's Association / STEM Co-Lead" — a slash doing the
    work of a sentence.

    The role belongs in the description, where it can be a clause instead of a
    label: "As STEM Co-Lead, organised STEM-focused events including..."
    """

    PROFILE = {"identity": {"name": "Safin"}, "contact": {},
               "skill_categories": [], "experience": [], "education": [],
               "projects": [], "certificates": [], "volunteer": []}

    def _md(self, volunteer):
        resume = {"summary": "", "skills": [], "experience": [], "education": [],
                  "projects": [], "certificates": [], "volunteer": volunteer}
        return to_markdown(resume, self.PROFILE, "Safin")

    def test_the_heading_is_the_organisation_alone(self):
        markdown = self._md([{
            "organization": "Al-Azhar Garden Student's Association",
            "description": "As STEM Co-Lead, organised STEM-focused events.",
        }])

        assert "### Al-Azhar Garden Student's Association" in markdown
        assert "/ STEM Co-Lead" not in markdown

    def test_the_role_reads_as_prose(self):
        markdown = self._md([{
            "organization": "Al-Azhar Garden Student's Association",
            "description": "As STEM Co-Lead, organised STEM-focused events.",
        }])

        assert "As STEM Co-Lead, organised STEM-focused events." in markdown

    def test_a_role_sent_separately_is_folded_into_the_sentence(self):
        """The model is asked to write it into the description. If it hands back a
        bare role anyway, the role still gets onto the page — as a clause, not a
        heading."""
        markdown = self._md([{
            "organization": "Al-Azhar Garden CERT",
            "role": "Team Member",
            "description": "Coordinated emergency preparedness for 200+ households.",
        }])

        assert "As Team Member, coordinated emergency preparedness" in markdown
        assert "/ Team Member" not in markdown

    def test_a_role_already_in_the_sentence_is_not_repeated(self):
        markdown = self._md([{
            "organization": "Al-Azhar Garden CERT",
            "role": "Team Member",
            "description": "As a Team Member, coordinated emergency preparedness.",
        }])

        assert markdown.count("Team Member") == 1

    def test_the_shape_tells_the_model_to_write_it_that_way(self):
        """The renderer's fold-in is a safety net, not the plan. The plan is that
        the model writes a sentence."""
        shape = resume_schema.SHAPE["volunteer"][0]

        assert "role" not in shape
        assert "Work my ROLE into the sentence" in shape["description"]


class TestTheSummaryIsAHeadlineNotABiography:
    """What the model produced:

        "Safin Mahesania is a junior software developer with experience in both
        frontend and backend development, specializing in cross-platform mobile
        apps and distributed systems. With a background in Java, Flutter, and
        Python, Safin Mahesania excels at integrating APIs, databases, and AI
        tools to create scalable solutions."

    The name. Twice. In the third person. On a page where the name is already the
    largest thing on it, in twenty-point type, four lines above.

    It was invited to. In redacted mode the fact sheet hands the model "{{NAME}}"
    and tells it to write that wherever a name belongs — which reads as permission
    to put one in the prose. The substitution then turns it into a real name, and
    the resume introduces you to yourself.
    """

    PROFILE = {
        "identity": {"name": "Safin Mahesania"},
        "contact": {"city": "Montreal"},
        "skill_categories": [], "experience": [], "education": [],
        "projects": [], "certificates": [], "volunteer": [],
    }

    def _md(self, summary, name="Safin Mahesania"):
        resume = {"summary": summary, "skills": [], "experience": [],
                  "education": [], "projects": [], "certificates": [],
                  "volunteer": []}
        return to_markdown(resume, self.PROFILE, name)

    def test_the_name_is_stripped_from_the_summary(self):
        markdown = self._md(
            "Safin Mahesania is a junior software developer with experience in "
            "both frontend and backend development.")

        summary = markdown.split("## Summary")[1]

        assert "Safin Mahesania" not in summary

    def test_the_sentence_starts_with_the_noun(self):
        """Strip the name and "is a" is left dangling. The sentence should begin
        where it always should have — at "Junior software developer"."""
        markdown = self._md(
            "Safin Mahesania is a junior software developer with backend experience.")

        assert "Junior software developer with backend experience." in markdown

    def test_the_name_appearing_twice_is_handled(self):
        markdown = self._md(
            "Safin Mahesania is a developer. With a background in Java, "
            "Safin Mahesania excels at APIs.")

        assert markdown.split("## Summary")[1].count("Safin Mahesania") == 0

    def test_the_redaction_placeholder_is_stripped_too(self):
        """This is where it actually comes from: the model writes {{NAME}} because
        it was told to, and fill_contact turns it into a name afterwards."""
        markdown = self._md("{{NAME}} is a backend developer who ships.",
                            name="{{NAME}}")

        assert "{{NAME}}" not in markdown.split("## Summary")[1]
        assert "Backend developer who ships." in markdown

    def test_a_summary_that_was_already_right_is_left_alone(self):
        """The strip is a safety net, not an editor. It must not mangle prose that
        was correct to begin with."""
        good = ("Junior software developer across frontend and backend, with "
                "cross-platform mobile and distributed systems experience.")

        assert good in self._md(good)

    def test_the_shape_tells_the_model_not_to(self):
        """The strip guarantees the name is gone. Only the prompt can make the
        sentence good — and a net that catches the failure is not a substitute for
        not failing."""
        shape = resume_schema.SHAPE["summary"]

        assert "NEVER write my name" in shape
        assert "headline, not a biography" in shape
