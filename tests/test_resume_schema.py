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

    def test_the_project_owner_is_rendered_not_annotated(self):
        """"(owner: course)" once reached a real resume verbatim."""
        markdown = to_markdown(HONEST, PROFILE, "Safin Mahesania")

        assert "Recipedia - Course (Flutter, Dart)" in markdown
        assert "owner:" not in markdown

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
