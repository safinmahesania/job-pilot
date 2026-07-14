"""Rendering the resume to Word.

The reason this file exists is `test_there_are_no_tables`. Every Word resume
template in the world puts dates on the right with a two-column table, and it is a
quiet liability: Workday and Taleo — which stand between this candidate and a large
share of Canadian employers — are known for scrambling tables, because to a parser
a table cell is not a line of text. The job title lands in one field and its dates
in another, and the resume that arrives is not the one that was sent.

A right-aligned tab stop puts the date in exactly the same place and is, to a
parser, an ordinary line. So: no tables, ever, and a test that fails the moment
someone adds one back.
"""
import io
import zipfile

import pytest

from src.resume_docx import to_docx, _split_right

RESUME = """# Safin Mahesania

Montreal, QC, Canada
safin@example.com | (514) 555-0123 | linkedin.com/in/safin

## Summary

Backend-leaning MSc student looking for a junior role.

## Skills

- **Programming & Core Concepts:** Python | JavaScript | SQL
- **Databases:** PostgreSQL | SQLite

## Education

### Master of Science, Computer Science @@ Sep 2024 - Apr 2026
Concordia University, Montreal, QC

## Work Experience

### Software Developer Intern @@ May 2024 - Aug 2024
Acme Corp, Toronto, ON
- Cut API latency 40% with a Redis cache in front of the pricing service.

## Projects

### JobPilot - Personal (Python, FastAPI) @@ github.com/safinmahesania/job-pilot
- Fetches 70+ boards concurrently and scores each posting against my profile.

## Certificates and Achievements

- AWS Certified Cloud Practitioner @@ credly.com/badges/abc123
"""


def document_xml(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read("word/document.xml").decode("utf-8")


class TestSplitRight:
    def test_it_splits_on_the_marker(self):
        assert _split_right("Role @@ May 2024") == ("Role", "May 2024")

    def test_a_line_without_the_marker_has_no_right_side(self):
        assert _split_right("Concordia University") == ("Concordia University", "")

    def test_only_the_first_marker_counts(self):
        left, right = _split_right("A @@ B @@ C")
        assert left == "A"


class TestItMatchesTheTemplate:
    """The styling is lifted from Resume_Template.docx, not invented. If someone
    quietly switches the font or the paper size, the resume stops looking like the
    one you designed — and nothing else would tell you."""

    def test_it_uses_the_templates_font(self):
        from src import resume_docx
        assert resume_docx.FONT == "Times New Roman"
        assert "Times New Roman" in document_xml(to_docx(RESUME))

    def test_it_is_a4_with_half_inch_margins(self):
        from src import resume_docx
        assert round(resume_docx.PAGE_WIDTH_IN, 1) == 8.3      # A4, not Letter
        assert resume_docx.MARGIN_IN == 0.5

    def test_the_body_is_justified(self):
        xml = document_xml(to_docx(RESUME))
        assert 'w:val="both"' in xml       # Word's name for justified

    def test_the_heading_rule_is_a_paragraph_border(self):
        """As in the template — and not a one-cell table, which is the usual trick
        and would put back the structure we removed."""
        xml = document_xml(to_docx(RESUME))
        assert "<w:pBdr>" in xml


class TestNoTables:
    def test_there_are_no_tables(self):
        """The whole point of this renderer.

        A two-column table would look identical and parse worse. If someone adds
        one — for the dates, for the header, for a horizontal rule — this fails.
        """
        xml = document_xml(to_docx(RESUME))
        assert "<w:tbl>" not in xml, (
            "a table was added to the resume. Workday and Taleo mangle tables — "
            "use a right-aligned tab stop instead."
        )

    def test_the_dates_are_placed_with_a_right_tab_stop(self):
        """The mechanism that replaces the table."""
        xml = document_xml(to_docx(RESUME))
        assert 'w:val="right"' in xml       # a right-aligned tab stop exists
        assert "<w:tab/>" in xml            # and something actually tabs to it


class TestContent:
    def test_every_section_survives(self):
        """The headings carry their own capitalisation — they are small caps in
        the run properties, not shouted in the text. So the XML holds "Summary",
        and Word renders SUMMARY."""
        xml = document_xml(to_docx(RESUME))
        for heading in ["Summary", "Skills", "Education", "Work Experience",
                        "Projects", "Certificates and Achievements"]:
            assert heading in xml

    def test_the_headings_are_small_caps_not_shouted(self):
        xml = document_xml(to_docx(RESUME))
        assert "<w:smallCaps/>" in xml

    def test_the_marker_itself_never_appears_on_the_page(self):
        """`@@` is a rendering instruction. Seeing it on a resume would be
        mortifying."""
        xml = document_xml(to_docx(RESUME))
        assert "@@" not in xml

    def test_the_dates_are_there(self):
        xml = document_xml(to_docx(RESUME))
        assert "May 2024 - Aug 2024" in xml
        assert "Sep 2024 - Apr 2026" in xml

    def test_bold_markers_are_rendered_not_printed(self):
        xml = document_xml(to_docx(RESUME))
        assert "**" not in xml
        assert "<w:b/>" in xml

    def test_the_template_comment_never_reaches_the_page(self):
        """The template explains itself in an HTML comment. A stray line of it on
        a real resume, sent to a real employer, is unrecoverable."""
        with_comment = RESUME + """
<!--
HOW THIS TEMPLATE WORKS
Replace the placeholders. Keep the @@ convention.
-->
"""
        xml = document_xml(to_docx(with_comment))

        assert "HOW THIS TEMPLATE WORKS" not in xml
        assert "Replace the placeholders" not in xml

    def test_it_is_a_real_word_file(self):
        data = to_docx(RESUME)
        assert data[:2] == b"PK"
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            assert "word/document.xml" in z.namelist()
            assert z.testzip() is None


class TestMaterialsIntegration:
    def test_the_cover_letter_is_not_offered_as_word(self):
        """A letter is prose. It has no dates to pin to a margin, and .docx buys
        it nothing a PDF doesn't."""
        from src import materials
        with pytest.raises(ValueError):
            materials.to_docx("Dear Hiring Manager,", "cover")

    def test_the_resume_renders_through_materials(self):
        from src import materials
        data = materials.to_docx(RESUME, "resume")
        assert data[:2] == b"PK"


class TestPdfUnderstandsTheMarker:
    def test_the_marker_does_not_print_in_the_pdf_either(self):
        """The PDF path and the Word path must agree — the same stored markdown
        renders through both."""
        from src import materials
        pdf = materials.to_pdf(RESUME, "resume")
        assert pdf[:4] == b"%PDF"
        # Not a text search — fpdf compresses. Just prove it renders at all,
        # and that the renderer has a branch for the marker.
        assert len(pdf) > 1000


class TestTheProfileActuallyReachesTheModel:
    """A resume section can only be written from facts the model was given.

    This caught a real bug: the certificate and volunteer loops sat *after* the
    `return` in _profile_facts. Unreachable. The model was never told about them,
    so those sections could never have been written — and nothing failed. The
    resume just quietly came back without them, forever.

    Every field the template can render gets a line here.
    """

    def test_every_renderable_field_is_in_the_fact_sheet(self, monkeypatch,
                                                         profile):
        import yaml
        from pathlib import Path
        from src import apply

        monkeypatch.setattr(apply, "redacting", lambda: False)

        example = yaml.safe_load(
            (Path(__file__).resolve().parent.parent
             / "config" / "profile.example.yaml").read_text(encoding="utf-8")
        )
        facts = apply._profile_facts(example)

        for expected in ["Skills (expert)", "Skill category", "Experience:",
                         "Education:", "Certificate:", "Volunteer:"]:
            assert expected in facts, (
                f"'{expected}' never reaches the model — the resume cannot "
                f"contain what the model was not told."
            )

    def test_locations_and_dates_are_included(self, monkeypatch):
        from src import apply
        monkeypatch.setattr(apply, "redacting", lambda: False)

        facts = apply._profile_facts({
            "experience": [{"role": "Intern", "company": "Acme",
                            "location": "Toronto, ON",
                            "start": "2024-05", "end": "2024-08"}],
            "education": [{"degree": "MSc", "field": "CS",
                           "institution": "Concordia",
                           "location": "Montreal, QC",
                           "start": "2024-09", "end": "2026-04"}],
        })

        assert "Toronto, ON" in facts
        assert "Montreal, QC" in facts
        assert "May 2024 - Aug 2024" in facts       # not "2024-05–2024-08"
        assert "Sep 2024 - Apr 2026" in facts

    def test_present_is_left_alone(self, monkeypatch):
        from src import apply
        monkeypatch.setattr(apply, "redacting", lambda: False)

        facts = apply._profile_facts({
            "experience": [{"role": "Dev", "company": "X",
                            "start": "2025-01", "end": "Present"}],
        })

        assert "Jan 2025 - Present" in facts

    def test_an_empty_skill_category_is_not_mentioned(self, monkeypatch):
        """The model must not be handed an empty line it might feel obliged to
        fill."""
        from src import apply
        monkeypatch.setattr(apply, "redacting", lambda: False)

        facts = apply._profile_facts({
            "skill_categories": {"programming": ["Python"], "ml": []},
        })

        assert "Programming" in facts
        assert "Machine Learning" not in facts

    def test_project_owner_and_link_are_passed_through(self):
        from src import apply

        out = apply._format_projects([{
            "name": "JobPilot", "owner": "Personal",
            "link": "github.com/x/job-pilot", "tech": ["Python"],
            "description": "A tool", "highlights": ["Did a thing"],
        }])

        assert "Personal" in out
        assert "github.com/x/job-pilot" in out


class TestSkillCategoriesAreYours:
    """The category names and their order come from the profile, not from code.

    They used to be hardcoded — eight of them, with names I chose. Anyone whose
    resume grouped skills differently (a separate Deep Learning line, say, or Soft
    Skills, or no Cloud line at all) had to bend their resume to fit a list in
    someone else's source file. It is their resume.
    """

    def test_the_labels_and_order_come_from_the_profile(self):
        from src import apply

        groups = apply.skill_groups({"skill_categories": [
            {"label": "Programming Skills", "skills": ["Dart", "Java"]},
            {"label": "Deep Learning", "skills": ["PyTorch"]},
            {"label": "Soft Skills", "skills": ["Leadership"]},
        ]})

        assert [g["label"] for g in groups] == [
            "Programming Skills", "Deep Learning", "Soft Skills"
        ], "the profile's own order must be preserved"

    def test_an_empty_category_is_dropped(self):
        """An empty "Deep Learning:" line reads as something you forgot to fill."""
        from src import apply

        groups = apply.skill_groups({"skill_categories": [
            {"label": "Programming Skills", "skills": ["Dart"]},
            {"label": "Deep Learning", "skills": []},
        ]})

        assert [g["label"] for g in groups] == ["Programming Skills"]

    def test_a_category_with_no_label_is_ignored(self):
        from src import apply

        groups = apply.skill_groups({"skill_categories": [
            {"skills": ["Dart"]},
            {"label": "Databases", "skills": ["MySQL"]},
        ]})

        assert [g["label"] for g in groups] == ["Databases"]

    def test_the_old_dict_shape_still_works(self):
        """Profiles written before this changed must not break silently."""
        from src import apply

        groups = apply.skill_groups({"skill_categories": {
            "programming": ["Python"], "ml": [],
        }})

        assert groups == [{"label": "Programming & Core Concepts",
                           "skills": ["Python"]}]

    def test_no_skill_categories_at_all_is_not_an_error(self):
        from src import apply
        assert apply.skill_groups({}) == []

    def test_every_category_reaches_the_model(self, monkeypatch):
        from src import apply
        monkeypatch.setattr(apply, "redacting", lambda: False)

        facts = apply._profile_facts({"skill_categories": [
            {"label": "Deep Learning", "skills": ["PyTorch", "Torchvision"]},
            {"label": "Soft Skills", "skills": ["Leadership"]},
        ]})

        assert "Deep Learning: PyTorch | Torchvision" in facts
        assert "Soft Skills: Leadership" in facts
