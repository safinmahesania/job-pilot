"""Generate application documents (cover letter, and later resume tailoring).

Design goals from the spec:
  * Cover letter follows ONE fixed structure; only the content changes per job.
  * Grounded strictly in the candidate profile — no invented facts, no filler.
  * Projects are SELECTED (not invented) from the profile based on the job's
    relevance, because the profile usually lists more projects than fit on one
    application.

Everything runs through src.llm, which prefers a free hosted model and falls
back to local Ollama.
"""
import json
import re

from src import llm
from src.config import load_profile
from src.paths import COVER_LETTER_WORDS


# ── Helpers ──────────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    """Job descriptions are stored as HTML; flatten to plain text for the model."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _profile_facts(profile: dict) -> str:
    """Render the profile as a compact, unambiguous fact sheet for the model.

    Only fields that exist are included, so the model can't 'fill in' blanks.
    """
    lines = []
    ident = profile.get("identity", {})
    if ident.get("name"):
        lines.append(f"Name: {ident['name']}")
    if ident.get("titles"):
        lines.append(f"Target titles: {', '.join(ident['titles'])}")
    if profile.get("summary"):
        lines.append(f"Summary: {profile['summary'].strip()}")

    skills = profile.get("skills", {})
    flat = []
    for tier in ("expert", "proficient", "familiar"):
        flat += skills.get(tier, []) or []
    if flat:
        lines.append(f"Skills: {', '.join(flat)}")

    for exp in profile.get("experience", []) or []:
        role = exp.get("role", "")
        company = exp.get("company", "")
        span = f"{exp.get('start','')}–{exp.get('end','')}".strip("–")
        head = f"Experience: {role} at {company} ({span})".strip()
        lines.append(head)
        for h in exp.get("highlights", []) or []:
            lines.append(f"  - {h}")

    for edu in profile.get("education", []) or []:
        lines.append(
            f"Education: {edu.get('degree','')} in {edu.get('field','')}, "
            f"{edu.get('institution','')} ({edu.get('end','')})".strip()
        )
    return "\n".join(lines)


def _projects_block(profile: dict) -> str:
    """Numbered project list the model chooses from (it must not invent projects)."""
    out = []
    for i, p in enumerate(profile.get("projects", []) or []):
        tech = ", ".join(p.get("tech", []) or [])
        desc = p.get("description", "")
        highlights = "; ".join(p.get("highlights", []) or [])
        out.append(f"[{i}] {p.get('name','')} — {desc} "
                   f"(tech: {tech}) {highlights}".strip())
    return "\n".join(out) if out else "(no projects listed)"


# ── Cover letter ─────────────────────────────────────────────────────────────

_COVER_SYSTEM = (
    "You are a careful assistant that writes job cover letters for a candidate. "
    "Absolute rules:\n"
    "1. Use ONLY facts explicitly provided in the candidate profile. Never "
    "invent employers, dates, job titles, metrics, certifications, or skills.\n"
    "2. If the job asks for something the profile does not contain, do not claim "
    "it. Do not exaggerate.\n"
    "3. Do not add placeholders, brackets, or notes to the candidate. Output a "
    "finished letter only.\n"
    "4. Follow the exact structure given. Change only the wording to fit this "
    "specific job and company.\n"
    "5. Keep it to roughly {words} words, professional and plain — no clichés "
    "like 'I am thrilled' or 'perfect fit'."
)

# The single fixed structure every letter follows. Only the content varies.
_COVER_TEMPLATE = """Structure to follow exactly:

Dear Hiring Manager,

[Paragraph 1 — one or two sentences: state the role being applied for by name and
a one-line reason the candidate is a relevant applicant, grounded in the profile.]

[Paragraph 2 — connect 2-3 of the candidate's actual skills/experiences/projects
to specific needs in the job description. Reference only real items from the
profile. This is where the most relevant selected project(s) go.]

[Paragraph 3 — one or two sentences on motivation for this company/role, then a
brief, confident closing line offering to discuss further.]

Sincerely,
{name}"""


def generate_cover_letter(job: dict) -> dict:
    """Produce a grounded cover letter for one job.

    `job` needs at least: title, company, description.
    Returns {"text": <letter>, "provider": <name>}.
    """
    profile = load_profile()
    name = profile.get("identity", {}).get("name", "")

    jd = _strip_html(job.get("description", ""))[:6000]   # keep prompt bounded

    system = _COVER_SYSTEM.format(words=COVER_LETTER_WORDS)
    user = f"""CANDIDATE PROFILE (the only facts you may use):
{_profile_facts(profile)}

CANDIDATE PROJECTS (choose the most relevant; do not invent others):
{_projects_block(profile)}

JOB:
Title: {job.get('title','')}
Company: {job.get('company','')}
Description: {jd}

{_COVER_TEMPLATE.format(name=name)}

Write the finished cover letter now, following the structure exactly."""

    text, provider = llm.generate(system, user)
    return {"text": text, "provider": provider}


# ── Project selection (shared with resume tailoring, coming next) ────────────

def select_relevant_projects(job: dict, top_n: int = 3) -> list[int]:
    """Ask the model which project indices best match the job.

    Returns a list of indices into profile['projects']. Used by both the cover
    letter (implicitly) and, later, resume tailoring. Never invents projects —
    it only ranks the ones that exist.
    """
    profile = load_profile()
    projects = profile.get("projects", []) or []
    if not projects:
        return []

    jd = _strip_html(job.get("description", ""))[:4000]
    system = ("You rank a candidate's real projects by relevance to a job. "
              "Return ONLY a JSON array of integer indices, most relevant first. "
              "Use only the indices shown. Do not invent projects.")
    user = (f"JOB: {job.get('title','')} at {job.get('company','')}\n{jd}\n\n"
            f"PROJECTS:\n{_projects_block(profile)}\n\n"
            f"Return the top {top_n} indices as a JSON array, e.g. [2,0,1].")

    try:
        text, _ = llm.generate(system, user)
        match = re.search(r"\[[\d,\s]*\]", text)
        if match:
            idxs = json.loads(match.group(0))
            return [i for i in idxs if isinstance(i, int) and 0 <= i < len(projects)][:top_n]
    except Exception:
        pass
    # Fallback: first N in profile order.
    return list(range(min(top_n, len(projects))))
