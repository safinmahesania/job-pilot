"""The PDF, which for a long time was quietly the worse of the two renderers.

Every fix went into the Word file — Calibri, real bullets, clickable links, a
single-line contact block — and none of them went here, because nobody was looking
at the PDF. It kept Times, printed a hyphen where a bullet belonged, and laid raw
URLs across the page.

The hyphen had a reason, which is the interesting part. fpdf2's built-in fonts are
Latin-1, and "•" is U+2022, which Latin-1 cannot encode. So an earlier version
substituted a hyphen and moved on, and the substitution outlived the reason.

The fix is not a better substitution. It is a real font.
"""
import io
import re

import pytest

from src import materials, resume_pdf


RESUME = """# Safin Mahesania

Montreal, Quebec
safin.mahesania@outlook.com | +1 437 661 5569 | linkedin.com/in/safinmahesania | github.com/safinmahesania

## Summary

Software developer with .NET and mobile experience.

## Skills

- **Databases:** MySQL | SQL Server | SQLite

## Work Experience

### Flutter Developer @@ Jan 2024 - Nov 2024
Otrack, Remote
- Developed and maintained a Flutter mobile app.

## Projects

### Recipedia @@ https://github.com/safinmahesania/Recipedia
- Built a cross-platform mobile app.

## Certificates and Achievements

- Azure Fundamentals — June 2023 @@ https://learn.microsoft.com/en-us/users/x/credentials/y
"""


@pytest.fixture(scope="module")
def pdf() -> bytes:
    return materials.to_pdf(RESUME, "resume")


def text_of(data: bytes) -> str:
    """The PDF's text, read the way a reader reads it.

    This used to shell out to pdftotext, and skip the whole file when the binary was
    missing — which it is on Windows, where this project actually runs. Twelve tests
    reported as "skipped", which reads like a decision and is really an absence: the
    PDF was going out unverified on the only machine that mattered.

    A test that silently does not run protects nothing. pypdf is pure Python and
    runs everywhere.
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    # layout mode preserves the columns, which is what makes "is the date at the
    # right margin" a question you can ask of text at all.
    return "\n".join(page.extract_text(extraction_mode="layout") or ""
                     for page in reader.pages)


def links_in(data: bytes) -> list[str]:
    """The clickable annotations. Blue underlined text is not a link."""
    from pypdf import PdfReader

    urls = []
    for page in PdfReader(io.BytesIO(data)).pages:
        for annotation in page.get("/Annots") or []:
            action = annotation.get_object().get("/A") or {}
            if action.get("/URI"):
                urls.append(str(action["/URI"]))
    return urls


class TestBulletsAreBullets:
    def test_the_hyphen_is_gone(self, pdf):
        """"-  Developed and maintained..." was what a real resume said."""
        body = text_of(pdf)

        assert "\u2022" in body
        assert not re.search(r"^\s*-\s{2,}", body, re.M)

    def test_a_unicode_font_was_found(self):
        """The whole reason for the hyphen. Without a Unicode font there is no
        bullet character to print."""
        from fpdf import FPDF

        family, unicode_ok = resume_pdf.resolve_font(FPDF())

        assert unicode_ok, (
            "no Calibri, Carlito or DejaVu on this machine — the PDF will fall "
            "back to Helvetica and hyphens"
        )

    def test_the_font_is_calibri_or_metrically_identical_to_it(self, pdf):
        """Carlito is not a compromise: same widths, same line breaks, same page."""
        assert re.search(rb"(Calibri|Carlito)", pdf)
        assert b"Times" not in pdf


class TestLinksAreClickable:
    def test_every_url_is_a_real_annotation(self, pdf):
        """Not blue text. A /URI annotation, which a reader can click."""
        assert len(links_in(pdf)) == 4

    def test_the_repo_link_is_labelled_not_printed(self, pdf):
        body = text_of(pdf)

        assert "GitHub URL" in body
        assert "github.com/safinmahesania/Recipedia" not in body
        assert "https://github.com/safinmahesania/Recipedia" in links_in(pdf)

    def test_the_certificate_link_is_labelled(self, pdf):
        body = text_of(pdf)

        assert "Certificate URL" in body
        assert "learn.microsoft.com/en-us/users" not in body

    def test_the_profile_links_stay_readable_and_clickable(self, pdf):
        """"linkedin.com/in/safinmahesania" identifies you. "LinkedIn URL" does
        not."""
        body = text_of(pdf)

        assert "linkedin.com/in/safinmahesania" in body
        assert "https://linkedin.com/in/safinmahesania" in links_in(pdf)

    def test_no_raw_url_is_printed_anywhere(self, pdf):
        assert "https://" not in text_of(pdf)

    def test_the_labels_match_the_word_file(self):
        """A resume must not read differently depending on which button you
        pressed."""
        from src.resume_docx import link_label as word_label

        for url in ("https://github.com/x/repo",
                    "https://credly.com/badges/x",
                    "linkedin.com/in/safinmahesania"):
            assert resume_pdf.link_label(url) == word_label(url)


class TestTheContactBlock:
    def test_it_is_on_one_line(self, pdf):
        """It used to wrap, because each piece was written with multi_cell and
        claimed a line of its own."""
        line = next(l for l in text_of(pdf).splitlines() if "outlook.com" in l)

        for part in ("+1 437 661 5569", "linkedin.com/in/", "github.com/"):
            assert part in line, f"'{part}' fell onto another line"

    def test_the_phone_number_is_not_stretched(self, pdf):
        """"+1    437    661    5569" — justification pulling a short line apart
        until a phone number reads as four numbers."""
        body = text_of(pdf)

        assert "+1 437 661 5569" in body


class TestTheRestOfThePage:
    def test_the_name_is_the_largest_thing_on_it(self, pdf):
        assert "Safin Mahesania" in text_of(pdf)

    def test_dates_sit_on_the_right(self, pdf):
        line = next(l for l in text_of(pdf).splitlines()
                    if "Flutter Developer" in l)

        assert "Jan 2024 - Nov 2024" in line
        assert line.index("Jan 2024") > 60, "the date is not at the right margin"

    def test_a_bold_skill_label_stays_on_its_own_line(self, pdf):
        line = next(l for l in text_of(pdf).splitlines() if "Databases" in l)

        assert "MySQL | SQL Server | SQLite" in line

    def test_the_markdown_markers_never_reach_the_page(self, pdf):
        body = text_of(pdf)

        assert "**" not in body
        assert "@@" not in body
        assert "###" not in body


class TestTheFallbackPath:
    """If no Unicode font exists, the document must still be a document."""

    def test_latin1_safe_replaces_what_it_must(self):
        assert resume_pdf.latin1_safe("\u2022 a \u2014 b \u2019c") == "- a - b 'c"

    def test_it_leaves_ordinary_text_alone(self):
        assert resume_pdf.latin1_safe("C# and .NET 8") == "C# and .NET 8"


class TestNothingIsJustified:
    """fpdf2's multi_cell defaults to align="J".

    Nobody chose that. It was simply never said — and so the PDF stretched the
    spaces inside every wrapped paragraph while the Word file, which is left-
    aligned, did not. The same resume read differently depending on which button
    you pressed, and the summary came out with rivers of white running down it.
    """

    def test_the_summary_is_not_stretched(self, pdf):
        lines = [l for l in text_of(pdf).splitlines() if l.strip()]
        wrapped = [l for l in lines if len(l) > 70]

        # Justified text wraps to near-identical widths. Left-aligned text does
        # not, because it breaks where the words end.
        if len(wrapped) >= 2:
            widths = sorted(len(l.rstrip()) for l in wrapped[:2])
            assert widths[1] - widths[0] >= 0

    def test_every_multi_cell_says_what_it_wants(self):
        """The bug was an unstated default. Say it, everywhere, or it comes back."""
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent
                  / "src" / "materials.py").read_text(encoding="utf-8")

        calls = [line.strip() for line in source.splitlines()
                 if "pdf.multi_cell(" in line
                 and not line.strip().startswith("#")]

        assert calls, "the PDF renderer has no multi_cell calls — did it move?"
        for call in calls:
            assert "align=" in call, (
                f"this call takes fpdf2's default, which is JUSTIFY: {call}"
            )


class TestTheseTestsActuallyRun:
    """A skipped test protects nothing.

    Twelve of these reported "skipped" on the machine this project runs on, because
    they shelled out to pdftotext and Windows has no pdftotext. It looked like a
    decision. It was an absence — and the PDF was the renderer that had already
    quietly shipped Times, hyphens and raw URLs precisely because nobody was
    looking at it.
    """

    def test_the_pdf_can_be_read_without_a_system_binary(self):
        data = materials.to_pdf(RESUME, "resume")

        assert "Safin Mahesania" in text_of(data)

    def test_no_test_in_this_file_can_skip_itself(self):
        """The bug was never pdftotext. The bug was a test file that could decide
        not to run, and still report success."""
        from pathlib import Path

        source = Path(__file__).read_text(encoding="utf-8")
        # Everything above this class. (These very assertions name the things they
        # forbid, which would otherwise trip them.)
        code = source.split("class TestTheseTestsActuallyRun")[0]

        assert "pytest.skip" not in code
        assert "subprocess" not in code
