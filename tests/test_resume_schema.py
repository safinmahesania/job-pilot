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

    def test_the_structured_check_passes_it(self):
        """The same facts, given as data. Nothing to misparse."""
        assert resume_guard.check_structured(HONEST, PROFILE) == []


class TestRealLiesAreStillCaught:
    """The rewrite is worthless if it made the guard blind."""

    def test_an_invented_employer(self):
        lying = dict(HONEST, experience=HONEST["experience"] + [
            {"role": "Sales Manager", "company": "TechCorp Inc.",
             "dates": "2020", "bullets": ["Led a sales team."]}])

        problems = resume_guard.check_structured(lying, PROFILE)

        assert any("TechCorp Inc." in p for p in problems)

    def test_an_invented_school(self):
        lying = dict(HONEST, education=HONEST["education"] + [
            {"degree": "BBA", "institution": "University of Toronto",
             "dates": "2015 - 2019"}])

        problems = resume_guard.check_structured(lying, PROFILE)

        assert any("University of Toronto" in p for p in problems)

    def test_a_skill_category_lifted_from_the_job(self):
        lying = dict(HONEST, skills=[
            {"label": "Sales Experience", "skills": ["Healthcare", "SaaS"]}])

        problems = resume_guard.check_structured(lying, PROFILE)

        assert any("Sales Experience" in p for p in problems)

    def test_deleting_your_real_work_history(self):
        """The model's other idea of a correction."""
        dropped = dict(HONEST, experience=[])

        problems = resume_guard.check_structured(dropped, PROFILE)

        assert any("Work Experience section is empty" in p for p in problems)

    def test_a_dropped_section_is_a_missing_key_now(self):
        """Unmissable — where in markdown it was indistinguishable from a heading
        the model had formatted its own way."""
        dropped = dict(HONEST, education=[])

        problems = resume_guard.check_structured(dropped, PROFILE)

        assert any("Education section is empty" in p for p in problems)


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
        markdown = to_markdown(HONEST, PROFILE, "{{NAME}}", redacted=True)

        assert "{{NAME}}" in markdown
        assert "{{EMAIL}}" in markdown
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


class TestEndToEnd:
    def test_the_model_returns_json_and_a_document_comes_out(self, conn,
                                                             monkeypatch,
                                                             capture_llm):
        from src import apply

        monkeypatch.setattr(apply, "load_profile", lambda: dict(
            PROFILE, summary="Software developer.",
            skills={"expert": ["Flutter", "Dart", "REST APIs"]}))
        monkeypatch.setattr(apply, "redacting", lambda: False)
        monkeypatch.setattr(apply, "extract_requirements",
                            lambda job: ["Flutter", "Dart", "REST APIs"])
        monkeypatch.setattr(apply, "select_relevant_projects",
                            lambda job, **kw: [0])
        capture_llm.reply = lambda system, user: (json.dumps(HONEST), "gemini")

        result = apply.generate_resume({
            "title": "Mobile Developer", "company": "Bench",
            "description": "Flutter, Dart, REST APIs.",
        })

        assert "### Flutter Developer" in result["text"]
        assert "Bank Alfalah" in result["text"]
        assert result["overruns"] == []

    def test_the_prompt_asks_for_json_not_markdown(self, conn, monkeypatch,
                                                   capture_llm):
        from src import apply

        monkeypatch.setattr(apply, "load_profile", lambda: dict(
            PROFILE, summary="Dev.", skills={"expert": ["Flutter"]}))
        monkeypatch.setattr(apply, "redacting", lambda: False)
        monkeypatch.setattr(apply, "extract_requirements", lambda job: ["Flutter"])
        monkeypatch.setattr(apply, "select_relevant_projects", lambda job, **kw: [0])
        capture_llm.reply = lambda system, user: (json.dumps(HONEST), "gemini")

        apply.generate_resume({"title": "Mobile Developer", "company": "Bench",
                               "description": "Flutter."})

        prompt = next(u for _, u, _ in capture_llm.calls if "JSON SHAPE" in u)

        assert "Output ONLY the JSON" in prompt
        assert "###" not in prompt.split("JSON SHAPE")[1], (
            "the model is still being shown the markdown convention it can no "
            "longer get wrong"
        )

    def test_it_retries_with_the_specific_problem_then_gives_up(self, conn,
                                                               monkeypatch,
                                                               capture_llm):
        from src import apply

        monkeypatch.setattr(apply, "load_profile", lambda: dict(
            PROFILE, summary="Dev.", skills={"expert": ["Flutter"]}))
        monkeypatch.setattr(apply, "redacting", lambda: False)
        monkeypatch.setattr(apply, "extract_requirements", lambda job: ["Flutter"])
        monkeypatch.setattr(apply, "select_relevant_projects", lambda job, **kw: [0])

        lying = json.dumps(dict(HONEST, experience=HONEST["experience"] + [
            {"role": "Sales Manager", "company": "TechCorp Inc.", "dates": "2020",
             "bullets": ["Sold things."]}]))
        capture_llm.reply = lambda system, user: (lying, "gemini")

        with pytest.raises(resume_guard.FabricationError):
            apply.generate_resume({"title": "Mobile Developer", "company": "Bench",
                                   "description": "Flutter."})

        retries = [u for _, u, _ in capture_llm.calls
                   if "YOUR PREVIOUS ATTEMPT WAS WRONG" in u]

        assert retries, "it gave up without telling the model what was wrong"
        assert "TechCorp Inc." in retries[0]
        assert "Do NOT delete a section" in retries[0]


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


class TestTheSkillsShape:
    """The crash on the first real job after the skills change.

        AttributeError: 'str' object has no attribute 'get'

    The SHAPE moved — skills became a list of label strings, because the contents
    come from the profile and there was never a reason to let the model retype them
    — and the guard did not move with it. It called .get() on a string and took down
    the request with a 502.

    That is what a schema living in two places costs. So each reader takes both
    shapes rather than trusting either, and this file holds the tests that would
    have caught it.
    """

    PROFILE = {
        "identity": {"name": "Safin"},
        "contact": {"city": "Montreal"},
        "skill_categories": [
            {"label": "Programming & Markup Languages", "skills": ["Dart", "Python"]},
            {"label": "Databases", "skills": ["MySQL", "SQLite"]},
        ],
        "experience": [{"company": "Otrack"}],
        "education": [{"institution": "Concordia University"}],
        "projects": [{"name": "Recipedia"}],
        "certificates": [], "volunteer": [],
    }

    BASE = {
        "summary": "Software developer.",
        "experience": [{"role": "Dev", "company": "Otrack", "location": "",
                        "dates": "2024", "bullets": ["Built an app."]}],
        "education": [{"degree": "MSc", "institution": "Concordia University",
                       "location": "", "dates": "2026"}],
        "projects": [{"name": "Recipedia", "owner": "course", "tech": [],
                      "link": "", "bullets": ["An app."]}],
        "certificates": [], "volunteer": [],
    }

    def test_the_guard_does_not_crash_on_string_labels(self):
        """The 502. A string has no .get()."""
        resume = dict(self.BASE, skills=["Databases"])

        problems = resume_guard.check_structured(resume, self.PROFILE)

        assert problems == []

    def test_the_guard_still_reads_the_older_dict_shape(self):
        """A model shown a JSON example will occasionally reach for the old shape
        anyway. Reading both costs one line."""
        resume = dict(self.BASE,
                      skills=[{"label": "Databases", "skills": ["MySQL"]}])

        assert resume_guard.check_structured(resume, self.PROFILE) == []

    def test_an_invented_category_is_still_caught_in_either_shape(self):
        """Tolerance of shape must not become tolerance of lies."""
        for skills in (["Sales Experience"],
                       [{"label": "Sales Experience", "skills": ["SaaS"]}]):
            resume = dict(self.BASE, skills=skills)

            problems = resume_guard.check_structured(resume, self.PROFILE)

            assert any("Sales Experience" in p for p in problems)

    def test_the_model_chooses_the_order(self):
        resume = dict(self.BASE,
                      skills=["Databases", "Programming & Markup Languages"])

        markdown = to_markdown(resume, self.PROFILE, "Safin")

        assert markdown.index("Databases:") < markdown.index("Programming &")

    def test_the_order_survives_the_dict_shape_too(self):
        """It used to be silently lost: str({"label": ...}) matches no category, so
        every model-chosen order fell back to the profile's."""
        resume = dict(self.BASE, skills=[{"label": "Databases"}])

        markdown = to_markdown(resume, self.PROFILE, "Safin")

        assert markdown.index("Databases:") < markdown.index("Programming &")

    def test_a_category_the_model_omitted_is_rendered_anyway(self):
        """Dropping a skill category is dropping a fact."""
        resume = dict(self.BASE, skills=["Databases"])

        markdown = to_markdown(resume, self.PROFILE, "Safin")

        assert "Programming & Markup Languages" in markdown

    def test_no_skills_at_all_still_renders_the_profile(self):
        resume = dict(self.BASE, skills=[])

        markdown = to_markdown(resume, self.PROFILE, "Safin")

        assert "Databases:" in markdown
        assert "Programming & Markup Languages:" in markdown

    def test_the_limits_check_does_not_crash_either(self):
        for skills in (["Databases"], [{"label": "Databases"}], []):
            resume = dict(self.BASE, skills=skills)
            assert resume_limits.check_structured(resume) == []

    def test_the_shape_the_prompt_asks_for_is_the_shape_the_guard_reads(self):
        """The bug in one line: these two drifted apart. If the prompt starts asking
        for dicts again, this fails before a user sees a 502."""
        shape = resume_schema.SHAPE["skills"]

        assert isinstance(shape, list)
        assert isinstance(shape[0], str), (
            "the prompt asks for skill objects but the renderer and the guard "
            "expect label strings"
        )
