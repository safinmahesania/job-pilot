"""How long each part of the resume is allowed to be — and whether it actually is.

Telling a model "keep the summary to three lines" does not work, and it is easy to
see why: a line does not exist until the text is rendered. The model is counting
words in its head against a page it cannot see. It will overshoot, confidently.

So this module does two things the prompt cannot:

  * Converts every limit into a character budget the model *can* reason about,
    derived from the real font metrics of the real page — 10.5pt on 7 inches of
    text width, measured, not guessed.

  * Measures the finished resume by wrapping it the way the renderer will, and
    reports exactly what overran and by how much.

What it deliberately does not do is truncate. A resume bullet cut off mid-sentence
is worse than a bullet that runs one line long, and it would be cut off in the one
document where a visible mistake costs the most. When something overruns, the
model is asked once to tighten it, and if it still overruns you are told — you can
then edit it yourself, which you can do, because the text is right there in the app.
"""
from dataclasses import dataclass

from src.paths import (
    RESUME_SUMMARY_LINES,
    RESUME_EXPERIENCE_BULLET_LINES,
    RESUME_PROJECT_BULLET_LINES,
    RESUME_VOLUNTEER_LINES,
    RESUME_PROJECTS_USED,
)

# The page, measured from the same constants the renderer uses — so the budgets
# move automatically if the template's font, size or margins ever change.
from src.resume_docx import BODY_PT as _BODY_PT, TEXT_WIDTH_IN as _TEXT_WIDTH_IN

_TEXT_WIDTH_MM = _TEXT_WIDTH_IN * 25.4
_BULLET_INDENT_MM = 0.25 * 25.4        # the bullet indent, as set by the renderer


def _char_width_mm() -> float:
    """Average character width at the body size, from the real font.

    Times New Roman at 11pt on A4 — the same face and size the template uses. Get
    this wrong and every limit downstream is wrong: a "three-line summary" would be
    measured against a page that does not exist.
    """
    from fpdf import FPDF

    pdf = FPDF(format="A4", unit="mm")
    pdf.add_page()
    pdf.set_font("Times", "", _BODY_PT)
    sample = ("Built a pipeline that fetches seventy boards concurrently and "
              "scores each posting against my profile using a provider chain. ")
    return pdf.get_string_width(sample) / len(sample)


def chars_per_line(indented: bool = False) -> int:
    width = _TEXT_WIDTH_MM - (_BULLET_INDENT_MM if indented else 0)
    return int(width / _char_width_mm())


def measure_lines(text: str, indented: bool = False) -> int:
    """How many rendered lines this text will actually occupy.

    Wrapped on word boundaries, the way the renderer wraps it — not
    len(text) / 110, which quietly under-counts every line that ends in a long word.
    """
    if not text or not text.strip():
        return 0

    limit = chars_per_line(indented)
    lines, current = 1, 0

    for word in text.split():
        need = len(word) if current == 0 else len(word) + 1
        if current + need > limit:
            lines += 1
            current = len(word)
        else:
            current += need
    return lines


@dataclass
class Overrun:
    where: str          # "Summary", "Experience bullet 2", ...
    allowed: int        # lines
    actual: int         # lines
    text: str

    @property
    def message(self) -> str:
        return (f"{self.where}: {self.actual} lines, limit is {self.allowed}. "
                f"Tighten it — don't drop the fact, say it in fewer words.")


def budgets() -> dict:
    """The character budgets to put in front of the model.

    The model cannot count rendered lines. It can count characters.
    """
    per_line = chars_per_line()
    per_bullet_line = chars_per_line(indented=True)

    return {
        "summary_chars": RESUME_SUMMARY_LINES * per_line,
        "summary_lines": RESUME_SUMMARY_LINES,
        "experience_bullet_chars": RESUME_EXPERIENCE_BULLET_LINES * per_bullet_line,
        "experience_bullet_lines": RESUME_EXPERIENCE_BULLET_LINES,
        "project_bullet_budget": list(RESUME_PROJECT_BULLET_LINES),
        "project_bullet_chars": [n * per_bullet_line
                                 for n in RESUME_PROJECT_BULLET_LINES],
        "bullets_per_project": len(RESUME_PROJECT_BULLET_LINES),
        "max_projects": RESUME_PROJECTS_USED,
        "volunteer_chars": RESUME_VOLUNTEER_LINES * per_line,
        "volunteer_lines": RESUME_VOLUNTEER_LINES,
        "chars_per_line": per_line,
    }


def _sections(markdown: str) -> dict[str, list[str]]:
    """Split the resume into its sections, keeping the lines of each."""
    out, current = {}, None
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            current = line[3:].strip().lower()
            out[current] = []
        elif current:
            out[current].append(line)
    return out


def check(markdown: str) -> list[Overrun]:
    """Everything in the finished resume that runs longer than it is allowed to."""
    problems: list[Overrun] = []
    sections = _sections(markdown)

    # ── Summary ─────────────────────────────────────────────────────────────
    summary = " ".join(l for l in sections.get("summary", []) if l.strip())
    if summary:
        actual = measure_lines(summary)
        if actual > RESUME_SUMMARY_LINES:
            problems.append(Overrun("Summary", RESUME_SUMMARY_LINES, actual, summary))

    # ── Experience: every bullet, individually ──────────────────────────────
    n = 0
    for line in sections.get("work experience", []):
        if not line.lstrip().startswith(("- ", "* ")):
            continue
        n += 1
        text = line.lstrip()[2:]
        actual = measure_lines(text, indented=True)
        if actual > RESUME_EXPERIENCE_BULLET_LINES:
            problems.append(Overrun(
                f"Experience bullet {n}", RESUME_EXPERIENCE_BULLET_LINES,
                actual, text))

    # ── Projects: how many, and how long each is allowed to be ──────────────
    projects: list[list[str]] = []
    for line in sections.get("projects", []):
        if line.startswith("### "):
            projects.append([])
        elif line.lstrip().startswith(("- ", "* ")) and projects:
            projects[-1].append(line.lstrip()[2:])

    if len(projects) > RESUME_PROJECTS_USED:
        problems.append(Overrun(
            "Projects", RESUME_PROJECTS_USED, len(projects),
            f"{len(projects)} projects — keep the {RESUME_PROJECTS_USED} most "
            f"relevant to this job and drop the rest."))

    # Each project gets the same shape: three points, of which two may run to two
    # lines and one is a single line. The budget is per bullet, inside a project —
    # not shared across the projects.
    allowed_bullets = len(RESUME_PROJECT_BULLET_LINES)
    for i, bullets in enumerate(projects):
        if len(bullets) > allowed_bullets:
            problems.append(Overrun(
                f"Project {i + 1}", allowed_bullets, len(bullets),
                f"{len(bullets)} bullet points — keep {allowed_bullets}."))

        for j, bullet in enumerate(bullets[:allowed_bullets]):
            allowed = RESUME_PROJECT_BULLET_LINES[j]
            actual = measure_lines(bullet, indented=True)
            if actual > allowed:
                problems.append(Overrun(
                    f"Project {i + 1}, bullet {j + 1}", allowed, actual, bullet))

    # ── Volunteer: the description under each entry ──────────────────────────
    entry, body = 0, []
    for line in sections.get("volunteer and community involvement", []) + ["### "]:
        if line.startswith("### "):
            if body:
                text = " ".join(b for b in body if b.strip())
                actual = measure_lines(text)
                if actual > RESUME_VOLUNTEER_LINES:
                    problems.append(Overrun(
                        f"Volunteer entry {entry}", RESUME_VOLUNTEER_LINES,
                        actual, text))
            entry += 1
            body = []
        else:
            body.append(line)

    return problems


def instructions() -> str:
    """The limits, written for the model, in units it can actually count."""
    b = budgets()
    per_bullet = chars_per_line(indented=True)

    project_rules = "\n".join(
        f"  - Point {i + 1}: at most {n} line{'s' if n != 1 else ''} "
        f"(~{n * per_bullet} characters)"
        for i, n in enumerate(b["project_bullet_budget"])
    )

    return f"""LENGTH — these are hard limits, not suggestions. A resume that runs
onto a second page because a bullet was two words too long is a worse resume.

A rendered line of this resume holds about {b['chars_per_line']} characters
({per_bullet} in an indented bullet). Count characters; the lines follow.

- SUMMARY: at most {b['summary_lines']} lines (~{b['summary_chars']} characters).
- EXPERIENCE: every bullet at most {b['experience_bullet_lines']} lines
  (~{b['experience_bullet_chars']} characters). Each bullet, not the section.
- PROJECTS: at most {b['max_projects']}, the most relevant to this job.
  Each project gets AT MOST {b['bullets_per_project']} bullet points, with these
  budgets:
{project_rules}
  The last point is a single line on purpose — a short closing line, so the entry
  lands rather than trailing off. This shape repeats for every project.

  These are CEILINGS, not quotas. If the profile gives you two points for a
  project, write two. NEVER pad to reach three, and never manufacture a point to
  fill the shape — an invented bullet is a lie on a resume, and a resume with two
  true points is worth more than one with three where the third is fiction.
- VOLUNTEER: each description at most {b['volunteer_lines']} lines
  (~{b['volunteer_chars']} characters).

Cutting to length means saying the same thing in fewer words. It does not mean
dropping the achievement, the metric, or the technology — those are the parts that
matter. Cut the adjectives and the throat-clearing first."""
