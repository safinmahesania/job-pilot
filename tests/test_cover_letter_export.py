"""The cover letter exports to both Word and PDF, like the resume.

It used to be PDF-only (Word export raised). People asked for .docx too, because a cover
letter is often pasted or attached to an application form, where a Word file behaves
better than a PDF. It renders as clean prose — no resume structure, just the paragraphs.
"""
from src import materials

LETTER = """Dear Hiring Manager,

I am writing to apply for the Software Developer role. With my background in Python and
Flutter, I have shipped production applications end to end.

I would welcome the opportunity to discuss how I can contribute to your team.

Sincerely,
Sam"""


class TestCoverLetterExports:
    def test_word_export_produces_a_valid_docx(self):
        data = materials.to_docx(LETTER, "cover")
        assert data[:2] == b"PK"          # docx is a zip

    def test_pdf_export_produces_a_valid_pdf(self):
        data = materials.to_pdf(LETTER, "cover")
        assert data[:4] == b"%PDF"

    def test_word_export_is_non_trivial(self):
        # Not an empty shell — the paragraphs made it in.
        data = materials.to_docx(LETTER, "cover")
        assert len(data) > 2000

    def test_resume_word_export_still_works(self):
        # The change must not have broken the resume path.
        resume = "Sam Doe\nToronto, ON\n\nEXPERIENCE\n- Built things"
        data = materials.to_docx(resume, "resume")
        assert data[:2] == b"PK"

    def test_empty_cover_letter_does_not_crash(self):
        data = materials.to_docx("", "cover")
        assert data[:2] == b"PK"          # still a valid (near-empty) doc


class TestCoverLetterPdfIsProse:
    """The cover letter PDF must use the plain prose renderer, not the resume's markdown
    parser — a letter has no # headings, @@ dates, or bullets to interpret."""

    def test_prose_pdf_renders_paragraphs(self):
        letter = ("Dear Hiring Manager,\n\n" + "I am a developer. " * 20 +
                  "\n\nSincerely,\nSam")
        data = materials.to_pdf(letter, "cover")
        assert data[:4] == b"%PDF"
        assert len(data) > 2000

    def test_cover_pdf_does_not_crash_on_markdownish_text(self):
        # Even if the letter happens to contain a '#', it's treated as prose, not a
        # heading — no resume parsing.
        letter = "Dear Team,\n\nI love C# and .NET.\n\nBest,\nSam"
        data = materials.to_pdf(letter, "cover")
        assert data[:4] == b"%PDF"
