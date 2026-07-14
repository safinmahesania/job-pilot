"""The resume as a PDF, matching the Word file rather than approximating it.

The two renderers had drifted apart, and only one of them was being fixed. The
Word file got Calibri, real bullets, clickable links and a single-line contact
block; the PDF kept Times, a hyphen where a bullet should be, and raw URLs printed
across the page — because it was written first, against fpdf2's built-in fonts,
which are Latin-1 and have no bullet character in them.

That is the whole reason for the hyphen. "•" is U+2022, and the core PDF fonts
cannot encode it, so an earlier version quietly substituted "-" and moved on. The
fix is not a better substitution — it is a real font.

So: find Calibri. It is on every Windows machine, which is where this runs. Failing
that, Carlito, which is metrically identical to Calibri and ships with most Linux
distributions. Failing that, DejaVu, which is merely present. Only if none of them
exist do we fall back to Helvetica and the hyphen, and at that point the document
is a fallback and says so by looking like one.
"""
import re
from pathlib import Path

#: Where Calibri actually lives, in the order worth trying. Regular and bold are
#: separate files — fpdf2 needs both, or bold text silently renders as regular.
FONT_CANDIDATES = [
    # Windows — where this runs.
    ("Calibri", "C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf"),
    # macOS, if Office is installed.
    ("Calibri", "/Library/Fonts/Calibri.ttf", "/Library/Fonts/Calibri Bold.ttf"),
    # Carlito: metric-compatible with Calibri, same widths, same line breaks.
    ("Carlito", "/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf",
     "/usr/share/fonts/truetype/crosextra/Carlito-Bold.ttf"),
    ("Carlito", "/usr/share/fonts/google-carlito/Carlito-Regular.ttf",
     "/usr/share/fonts/google-carlito/Carlito-Bold.ttf"),
    # DejaVu: not Calibri, but Unicode, so at least the bullets are bullets.
    ("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]

BULLET = "\u2022"


def resolve_font(pdf) -> tuple[str, bool]:
    """Register the best available font. Returns (family, is_unicode).

    is_unicode is what decides whether a bullet can be a bullet.
    """
    for family, regular, bold in FONT_CANDIDATES:
        if not (Path(regular).exists() and Path(bold).exists()):
            continue
        try:
            pdf.add_font(family, "", regular)
            pdf.add_font(family, "B", bold)
            return family, True
        except Exception:                      # a font that will not load is no font
            continue

    # Nothing found. Helvetica is Latin-1: no bullet, no em dash, no smart quotes.
    return "Helvetica", False


def latin1_safe(text: str) -> str:
    """What survives a Latin-1 core font. Only used on the fallback path."""
    return (text.replace("\u2022", "-")
                .replace("\u2014", "-").replace("\u2013", "-")
                .replace("\u2018", "'").replace("\u2019", "'")
                .replace("\u201c", '"').replace("\u201d", '"')
                .encode("latin-1", "replace").decode("latin-1"))


def link_label(url: str) -> str:
    """The same labels as the Word file. A resume should not read differently
    depending on which button you pressed."""
    from src.resume_docx import link_label as docx_label
    return docx_label(url)


def is_url(text: str) -> bool:
    from src.resume_docx import _is_url
    return _is_url(text)


def strip_md(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", re.sub(r"__(.+?)__", r"\1", text))
