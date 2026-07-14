"""Generated application documents, stored against the job they belong to.

The point of storing them (rather than regenerating on demand) is correctness as
much as cost: when the browser extension attaches a resume to an application, it
must attach the resume written for THAT job. Every material row is keyed by
job_id, and the extension asks for a file by job_id — so there is no path by
which one company receives another company's cover letter.

`UNIQUE (job_id, kind)` means regenerating overwrites: a job has exactly one
current resume and one current cover letter, never a stale pile to pick from.
"""
import re
import unicodedata

from src import store


KINDS = ("resume", "cover")

# Filenames the ATS will see. Kept boring and professional.
_FILE_LABEL = {"resume": "Resume", "cover": "Cover_Letter"}


# ── Storage ─────────────────────────────────────────────────────────────────

def save(job_id: int, kind: str, content: str, provider: str = "") -> dict:
    """Store (or replace) a document for one job."""
    if kind not in KINDS:
        raise ValueError(f"unknown material kind: {kind}")
    conn = store.connect()
    conn.execute(
        "INSERT INTO materials (job_id, kind, content, provider) VALUES (?,?,?,?) "
        "ON CONFLICT(job_id, kind) DO UPDATE SET "
        "content=excluded.content, provider=excluded.provider, "
        "created_at=datetime('now')",
        (job_id, kind, content, provider),
    )
    conn.commit()
    conn.close()
    return {"job_id": job_id, "kind": kind, "saved": True}


def get(job_id: int, kind: str) -> dict | None:
    """The current document of this kind for this job, or None."""
    conn = store.connect()
    conn.row_factory = None
    row = conn.execute(
        "SELECT content, provider, created_at FROM materials "
        "WHERE job_id=? AND kind=?", (job_id, kind),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {"kind": kind, "content": row[0], "provider": row[1], "created_at": row[2]}


def list_for(job_id: int) -> list[dict]:
    """What has been generated and saved for this job (without the bodies)."""
    conn = store.connect()
    conn.row_factory = None
    rows = conn.execute(
        "SELECT kind, provider, created_at, LENGTH(content) FROM materials "
        "WHERE job_id=? ORDER BY kind", (job_id,),
    ).fetchall()
    conn.close()
    return [{"kind": r[0], "provider": r[1], "created_at": r[2], "chars": r[3]}
            for r in rows]


def delete(job_id: int, kind: str) -> bool:
    conn = store.connect()
    cur = conn.execute("DELETE FROM materials WHERE job_id=? AND kind=?",
                       (job_id, kind))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return bool(n)


# ── Filenames ───────────────────────────────────────────────────────────────

def filename(job: dict, kind: str, ext: str) -> str:
    """A clean, recruiter-facing filename: Safin_Mahesania_Resume_Shopify.pdf"""
    from src.config import load_profile
    name = (load_profile().get("identity", {}) or {}).get("name", "")

    def slug(text: str) -> str:
        text = unicodedata.normalize("NFKD", str(text or ""))
        text = text.encode("ascii", "ignore").decode()
        text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
        return text[:40]

    parts = [p for p in (slug(name), _FILE_LABEL.get(kind, kind),
                         slug(job.get("company", ""))) if p]
    return "_".join(parts) + f".{ext}"


# ── PDF rendering ───────────────────────────────────────────────────────────
#
# Resumes are stored as Markdown (from the template) and cover letters as plain
# prose. Both render through the same small writer: this is deliberately a simple
# renderer, not a full Markdown engine — a resume only ever needs headings, bold,
# bullets and paragraphs, and a dependency-light path matters more than fidelity.

def _pdf_line_with_right(pdf, left: str, right: str, body_size: float,
                         face: str = "Helvetica"):
    """One line with `right` flush to the right margin — the tabbed-date line."""
    heading = left.startswith("### ")
    bullet = left.lstrip().startswith(("- ", "* "))

    if heading:
        left = left[4:].strip()
        pdf.set_font(face, "B", body_size + 0.5)
    elif bullet:
        # A "-", not a "•". Core PDF fonts are latin-1 and U+2022 isn't in it —
        # the rest of this renderer already made that concession, and a bullet
        # that differs by line would look like a mistake.
        left = "  -  " + left.lstrip()[2:]
        pdf.set_font(face, "", body_size)
    else:
        pdf.set_font(face, "", body_size)

    left = left.replace("**", "")
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    right_w = pdf.get_string_width(right) + 1

    pdf.cell(usable - right_w, 5, left, align="L")
    pdf.set_font(face, "I", body_size - 0.5)
    pdf.cell(right_w, 5, right, align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(face, "", body_size)


def to_docx(content: str, kind: str) -> bytes:
    """Render to a Word document.

    Only the resume gets this. A cover letter is prose — it has no dates to pin to
    a margin and nothing a .docx buys it that a PDF doesn't.
    """
    if kind != "resume":
        raise ValueError("Word export is for the resume; the cover letter is a PDF.")
    from src.resume_docx import to_docx as render
    return render(content)


def to_pdf(content: str, kind: str) -> bytes:
    """Render a document to PDF bytes. Raises if fpdf2 isn't installed."""
    try:
        from fpdf import FPDF
    except ImportError as e:      # pragma: no cover
        raise RuntimeError(
            "PDF export needs fpdf2. Install it with: pip install fpdf2"
        ) from e

    # The resume matches the Word template: Times, A4, half-inch margins. The
    # cover letter keeps the roomier layout — it is prose on its own page, and it
    # is not competing for space.
    from src.resume_docx import BODY_PT, MARGIN_IN, TEXT_WIDTH_IN   # noqa: F401

    if kind == "resume":
        pdf = FPDF(format="A4", unit="mm")
        margin = MARGIN_IN * 25.4
        pdf.set_margins(margin, margin, margin)
        pdf.set_auto_page_break(auto=True, margin=margin)
        body_size = BODY_PT
        face = "Times"
    else:
        pdf = FPDF(format="letter", unit="mm")
        pdf.set_margins(18, 16, 18)
        pdf.set_auto_page_break(auto=True, margin=15)
        body_size = 11
        face = "Helvetica"

    pdf.add_page()

    in_comment = False

    for raw_line in content.splitlines():
        line = raw_line.rstrip()

        # Skip HTML comment blocks whole — the template's instructions live in one,
        # and a stray line of them must never end up on a real resume.
        stripped = line.strip()
        if stripped.startswith("<!--"):
            in_comment = "-->" not in stripped
            continue
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue

        # Blank line -> vertical space.
        if not stripped:
            pdf.ln(2.5)
            continue

        text = _pdf_safe(line)

        # The `@@` convention: everything after it belongs at the right margin.
        # In Word this is a right tab stop; here it's a right-aligned cell on the
        # same line. Either way the reader sees the dates on the right — and a
        # parser sees one ordinary line of text, not a table cell.
        right = ""
        if "@@" in text:
            left_part, _, right_part = text.partition("@@")
            text = left_part.rstrip()
            right = right_part.strip()

        # Always start a block at the left margin: multi_cell(0, ...) measures its
        # width from the current x, and a preceding write() leaves x mid-line.
        pdf.set_x(pdf.l_margin)

        if right:
            _pdf_line_with_right(pdf, text, right, body_size, face)
            continue

        # # Name  -> title
        if text.startswith("# "):
            pdf.set_font(face, "B", 17)
            pdf.multi_cell(0, 8, text[2:].strip())
            pdf.ln(1)
        # ## Section
        elif text.startswith("## "):
            pdf.ln(2)
            pdf.set_font(face, "B", 11.5)
            pdf.multi_cell(0, 6, text[3:].strip().upper())
            # a rule under the section heading
            y = pdf.get_y()
            pdf.set_draw_color(180, 121, 26)          # brand
            pdf.set_line_width(0.4)
            pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
            pdf.ln(2)
        # ### Sub-heading (role, project name)
        elif text.startswith("### "):
            pdf.set_font(face, "B", body_size)
            pdf.multi_cell(0, 5.5, text[4:].strip())
        # - bullet
        elif re.match(r"^[-*]\s+", text):
            pdf.set_font(face, "", body_size)
            bullet = _strip_md(re.sub(r"^[-*]\s+", "", text))
            usable = pdf.w - pdf.l_margin - pdf.r_margin
            pdf.set_x(pdf.l_margin + 3)
            pdf.multi_cell(usable - 3, 5, f"-  {bullet}")
        # everything else: a paragraph
        else:
            # **Bold label:** rest  -> bold label, regular text, on one flowing line
            m = re.match(r"^\*\*(.+?):?\*\*:?\s*(.*)$", text)
            if m:
                pdf.set_font(face, "B", body_size)
                pdf.write(5, f"{m.group(1)}: ")
                pdf.set_font(face, "", body_size)
                pdf.write(5, _strip_md(m.group(2)))
                pdf.ln(5.5)
            else:
                pdf.set_font(face, "", body_size)
                pdf.multi_cell(0, 5.4, _strip_md(text))

    out = pdf.output()
    return bytes(out)


def _pdf_safe(text: str) -> str:
    """Core PDF fonts are latin-1 only; swap the characters models like to use."""
    replacements = {
        "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"', "\u2022": "-", "\u2026": "...",
        "\u00a0": " ", "\u2192": "->",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "ignore").decode("latin-1")


def _strip_md(text: str) -> str:
    """Drop inline Markdown markers the simple renderer doesn't draw."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)      # bold
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*", r"\1", text)   # italic
    text = re.sub(r"`(.+?)`", r"\1", text)            # code
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r"\1 (\2)", text)  # links
    return text.strip()
