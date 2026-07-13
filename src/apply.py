"""Generate application documents (cover letter; resume tailoring next).

Quality strategy — the letter is built in four steps, not one big prompt. A
single "here is the JD and my whole profile, write something" call reliably
produces vague, generic prose; narrowing the context first and then forcing the
model to critique its own draft is what makes the output read professionally.

  1. Extract the job's concrete requirements from the description.
  2. Rank my most recent projects against those requirements and pick the best.
  3. Write a draft with only those requirements + those projects in context.
  4. Critique the draft against a rubric and rewrite it.

Hard rules enforced throughout:
  * First person, always — the letter is me speaking as "I".
  * Only facts present in profile.yaml. Nothing invented.
  * No clichés or filler; every sentence carries a specific, checkable claim.
  * Every letter states that I am open to relocating.
"""
import json
import re

from src import llm
from src.config import load_profile
from src.paths import (
    CONFIG_DIR,
    COVER_LETTER_WORDS,
    COVER_LETTER_PROJECT_POOL,
    COVER_LETTER_PROJECTS_USED,
    COVER_LETTER_MENTION_RELOCATION,
    COVER_LETTER_REVISE,
    RESUME_TEMPLATE_FILE,
    RESUME_PROJECT_POOL,
    RESUME_PROJECTS_USED,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    """Job descriptions are stored as HTML; flatten to plain text, keeping the
    bullet structure (the requirement lists live in <li> tags)."""
    if not text:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.I)
    text = re.sub(r"<(br|/p|/div|/h\d)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"&[a-z]+;", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


# ── Redaction ───────────────────────────────────────────────────────────────
#
# A cover letter needs your background. It does not need your phone number.
#
# In redacted mode the model is given your skills, projects and employment history
# — it cannot write about you otherwise — but never your name, email, phone,
# address or profile links. Those stay as placeholders, and JobPilot substitutes
# them here, on this machine, after the model has finished. The hosted provider
# never receives a direct identifier.

def redacting() -> bool:
    """True when identifiers must be kept out of prompts."""
    return llm.privacy_mode() != "full"


def fill_contact(text: str, profile: dict) -> str:
    """Substitute the contact placeholders — locally, after generation."""
    contact = profile.get("contact", {}) or {}
    ident = profile.get("identity", {}) or {}

    values = {
        "{{NAME}}": ident.get("name", ""),
        "{{EMAIL}}": contact.get("email", ""),
        "{{PHONE}}": contact.get("phone", ""),
        "{{ADDRESS}}": contact.get("address", ""),
        "{{POSTAL_CODE}}": contact.get("postal_code", ""),
        "{{LINKEDIN}}": contact.get("linkedin", ""),
        "{{GITHUB}}": contact.get("github", ""),
        "{{WEBSITE}}": contact.get("website", ""),
        "{{LOCATION}}": ", ".join(
            p for p in (contact.get("city"), contact.get("province")) if p
        ),
        "{{LINKS}}": " · ".join(
            v for v in (contact.get("linkedin"), contact.get("github"),
                        contact.get("website")) if v
        ),
    }
    for token, value in values.items():
        text = text.replace(token, value)
    return text


def _profile_facts(profile: dict) -> str:
    """The profile as a compact fact sheet. Only fields that exist are included,
    so the model has no blanks to fill in.

    When redacting, the name becomes a placeholder and contact details are left
    out altogether — you do not need someone's phone number to write about their
    work.
    """
    lines = []
    ident = profile.get("identity", {}) or {}
    if redacting():
        lines.append("Name: {{NAME}}    <- a placeholder. Write it exactly like "
                     "that wherever a name belongs; it is filled in afterwards.")
    elif ident.get("name"):
        lines.append(f"Name: {ident['name']}")
    if ident.get("seniority"):
        lines.append(f"Level: {ident['seniority']}")
    if profile.get("summary"):
        lines.append(f"About me: {profile['summary'].strip()}")

    skills = profile.get("skills", {}) or {}
    for tier in ("expert", "proficient", "familiar"):
        if skills.get(tier):
            lines.append(f"Skills ({tier}): {', '.join(skills[tier])}")

    for exp in profile.get("experience", []) or []:
        span = f"{exp.get('start','')}–{exp.get('end','')}".strip("–")
        lines.append(
            f"Experience: {exp.get('role','')} at {exp.get('company','')} ({span})".strip()
        )
        for h in exp.get("highlights", []) or []:
            lines.append(f"  - {h}")

    for edu in profile.get("education", []) or []:
        lines.append(
            f"Education: {edu.get('degree','')} in {edu.get('field','')}, "
            f"{edu.get('institution','')} ({edu.get('end','')})".strip()
        )
    return "\n".join(lines)


def _format_projects(projects: list, indices=None) -> str:
    """Render projects (optionally only the selected indices) as numbered lines."""
    out = []
    for i, p in enumerate(projects):
        if indices is not None and i not in indices:
            continue
        tech = ", ".join(p.get("tech", []) or [])
        bits = [f"[{i}] {p.get('name', '')}"]
        if p.get("description"):
            bits.append(f"— {p['description']}")
        if tech:
            bits.append(f"(tech: {tech})")
        line = " ".join(bits)
        for h in p.get("highlights", []) or []:
            line += f"\n    - {h}"
        out.append(line)
    return "\n".join(out) if out else "(no projects listed)"


def _clean_output(text: str) -> str:
    """Strip artefacts models add despite instructions."""
    text = text.strip()
    text = re.sub(r"^```[a-z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = re.sub(r"^(here is|here's|sure,).*?:\s*\n+", "", text, flags=re.I)
    text = re.sub(r"^\*\*(.+?)\*\*$", r"\1", text, flags=re.M)
    text = re.sub(r"^(Subject|Re):.*\n+", "", text, flags=re.I)
    return text.strip()


# ── Step 1: what does this job actually ask for? ─────────────────────────────

def extract_requirements(job: dict, limit: int = 8) -> list[str]:
    """Pull the concrete, checkable requirements out of the job description.

    Concrete = technologies, tools, tasks, domains — not 'team player'.
    Returns [] on failure; the letter still works without it.
    """
    jd = _strip_html(job.get("description", ""))[:6000]
    if not jd:
        return []

    system = (
        "You extract concrete requirements from job descriptions. "
        "Return ONLY a JSON array of short strings — no prose, no markdown. "
        "Include specific technologies, tools, responsibilities and domain "
        "knowledge. Exclude generic filler ('team player', 'good communication', "
        "'fast-paced environment')."
    )
    user = (f"JOB: {job.get('title', '')} at {job.get('company', '')}\n\n{jd}\n\n"
            f"Return at most {limit} requirements as a JSON array of strings.")
    try:
        text, _ = llm.generate(system, user)
        match = re.search(r"\[.*\]", text, re.S)
        if match:
            items = json.loads(match.group(0))
            return [str(i).strip() for i in items if str(i).strip()][:limit]
    except Exception:
        pass
    return []


# ── Step 2: which of my recent projects evidence those requirements? ────────

def select_relevant_projects(job: dict,
                             top_n: int = COVER_LETTER_PROJECTS_USED,
                             requirements: list[str] | None = None,
                             pool: int = COVER_LETTER_PROJECT_POOL) -> list[int]:
    """Rank my REAL projects against the job and return their indices.

    Only the `pool` most recent projects are considered — profile.yaml lists
    projects newest-first, so older work never crowds out current work. Of those,
    the `top_n` most relevant are returned. Nothing is invented; the model only
    reorders indices that already exist.
    """
    profile = load_profile()
    projects = profile.get("projects", []) or []
    if not projects:
        return []

    pool_idx = list(range(min(pool, len(projects))))   # the latest N
    if len(pool_idx) <= top_n:
        return pool_idx

    reqs = "\n".join(f"- {r}" for r in (requirements or [])) or \
        _strip_html(job.get("description", ""))[:2500]

    system = ("You rank a candidate's real projects by how well they evidence a "
              "job's requirements. Return ONLY a JSON array of integer indices, "
              "most relevant first. Use only the indices shown. Invent nothing.")
    user = (f"JOB: {job.get('title', '')} at {job.get('company', '')}\n"
            f"REQUIREMENTS:\n{reqs}\n\n"
            f"MY MOST RECENT PROJECTS:\n"
            f"{_format_projects(projects, indices=set(pool_idx))}\n\n"
            f"Return the {top_n} most relevant indices as a JSON array, e.g. [2,0].")
    try:
        # Carries my project list -> local only.
        text, _ = llm.generate(system, user, personal=True)
        match = re.search(r"\[[\d,\s]*\]", text)
        if match:
            idxs = json.loads(match.group(0))
            picked = [i for i in idxs if isinstance(i, int) and i in pool_idx][:top_n]
            if picked:
                return picked
    except Exception:
        pass
    return pool_idx[:top_n]         # fallback: most recent, in profile order


# ── Step 3: write the draft ─────────────────────────────────────────────────

_VOICE_RULES = """You are {name}. This is YOUR cover letter — you are the applicant.

VOICE (the rule most often broken — read it twice):
- Write in the FIRST PERSON: "I", "my", "me".
- NEVER write "the candidate", "the applicant", "they", or your own name in the
  third person. You are not describing someone else. You ARE the person applying.

TRUTH:
- Use ONLY facts from the profile you are given. Never invent an employer, a date,
  a job title, a metric, a certification, a degree, or a skill.
- If the job asks for something the profile does not contain, say nothing about
  it. Do not imply it, do not hedge it — leave it out.

PLACEHOLDERS — if the profile gives you {{NAME}} instead of a real name, that is
deliberate. Write {{NAME}} exactly as it appears (in the signature, for example).
Do not invent a name, do not write "the candidate", do not leave the signature
blank. It is substituted for the real name after you are done.

OWN WORDS — do not copy and paste:
- The profile below is a set of NOTES, not sentences to reuse. Never lift a
  project description or a bullet verbatim. Rewrite everything in flowing prose,
  in my own voice, as I would explain it out loud to a person.
- A copied line reads like a resume glued into a letter. Explain instead: what the
  thing is, what I actually did, and why it matters for this job.

CRAFT — this is what separates a professional letter from a generated one:
- Every sentence must make a specific, checkable claim. If a sentence could sit in
  any other person's cover letter, it is dead weight — cut it.
- Show the work, don't label it. "I built a job-matching pipeline that scores
  postings against a profile using a local LLM" beats "I have strong experience
  with AI and backend development".
- Do not restate the resume as a list. Pick a small number of real things and
  explain what they demonstrate about how I work.
- Lead with substance, not with wanting the job. The reader knows I want the job.
- Concrete numbers only if they are already in the profile. Never invent one.
- Vary sentence length. Plain, confident, human. No corporate register.

BANNED — never use these, or anything like them:
  "I am thrilled/excited to apply", "perfect fit", "passionate about",
  "proven track record", "I believe I would be a great addition",
  "fast-paced environment", "wear many hats", "hit the ground running",
  "leverage my skills", "dynamic team player", "I am writing to express my
  interest" (find a real opening instead).

FORMAT:
- Output ONLY the finished letter. No preamble, no subject line, no notes to me,
  no square-bracket placeholders, no markdown, no bullet lists.
- Around {words} words. Three paragraphs of prose."""


_STRUCTURE = """Write exactly this shape:

Dear Hiring Manager,

PARAGRAPH 1 (2 sentences). Name the exact role and company. Then give the single
strongest concrete reason I am a credible applicant — a real thing I have built or
done, not a statement of enthusiasm or ambition.

PARAGRAPH 2 (4-6 sentences — the heart of the letter). Take the job's actual
requirements and match them to my real work, using the selected project(s) and my
experience. Name the specific technologies. Say what I built, what it does, and
what it demonstrates. This paragraph must be impossible to reuse for another job.

PARAGRAPH 3 (3-4 sentences). One specific reason this role or company interests
me, grounded in what the job actually involves — not flattery. {relocation}
Close by offering to discuss the role.

Sincerely,
{name}"""

_RELOCATION_LINE = ("Include one natural sentence stating that I am open to "
                    "relocating for this role if needed — state it plainly and "
                    "move on; do not dwell on it or apologise for it.")


# ── Step 4: critique and rewrite ────────────────────────────────────────────

_REVISE_SYSTEM = """You are a demanding hiring manager reviewing a cover letter
draft, then rewriting it to fix what you find.

Judge the draft against this rubric:
1. VOICE — is it entirely first person? Any trace of third person ("the candidate",
   the applicant's own name used as a subject) is a hard failure.
2. SPECIFICITY — could any sentence appear in a stranger's cover letter? Those
   sentences must be replaced with concrete, checkable claims or deleted.
3. EVIDENCE — does paragraph 2 show real work (what was built, with what, to what
   effect), or does it just list skills and adjectives?
3b. OWN WORDS — is any phrase lifted verbatim from the profile notes? Rewrite it
   in natural prose. The letter must never read like pasted resume bullets.
4. TRUTH — does it claim anything not in the profile? Cut it. Never add a fact
   that isn't in the profile, even if it would improve the letter.
5. CLICHÉ — remove any banned phrase or corporate filler.
6. RELOCATION — the letter must state plainly that I am open to relocating.
7. LENGTH — around {words} words, three prose paragraphs.

Then output ONLY the rewritten letter — first person, no commentary, no scores,
no markdown, no preamble. If the draft is already strong, still return the letter
(improved where you can), never a critique."""


def generate_cover_letter(job: dict) -> dict:
    """Produce a grounded, first-person cover letter for one job.

    `job` needs: title, company, description.
    Returns {"text", "provider", "requirements", "projects_used"}.
    """
    profile = load_profile()
    projects = profile.get("projects", []) or []
    real_name = (profile.get("identity", {}) or {}).get("name", "")

    # The name reaches the model only when redaction is off. Otherwise it sees the
    # placeholder everywhere — including in the system prompt and the signature.
    name = "{{NAME}}" if redacting() else real_name

    # Steps 1-2: narrow the context before writing anything.
    requirements = extract_requirements(job)
    picked = select_relevant_projects(job, requirements=requirements)

    jd = _strip_html(job.get("description", ""))[:5000]
    reqs_block = "\n".join(f"- {r}" for r in requirements) or "(see description below)"

    structure = _STRUCTURE.format(
        name=name,
        relocation=_RELOCATION_LINE if COVER_LETTER_MENTION_RELOCATION else "",
    )

    system = _VOICE_RULES.format(name=name or "the applicant",
                                 words=COVER_LETTER_WORDS)
    user = f"""MY PROFILE — the only facts I may use:
{_profile_facts(profile)}

MY MOST RELEVANT PROJECTS for this job (feature these; invent no others):
{_format_projects(projects, indices=set(picked))}

THE JOB
Role: {job.get('title', '')}
Company: {job.get('company', '')}

What this job actually asks for:
{reqs_block}

Full description (context):
{jd}

{structure}

Write my finished cover letter now, in the first person."""

    # Step 3: draft. The prompt contains my whole profile -> local only.
    draft, provider = llm.generate(system, user, personal=True)
    draft = _clean_output(draft)

    # Step 4: critique + rewrite. If this fails for any reason, keep the draft.
    text = draft
    if COVER_LETTER_REVISE:
        try:
            revised, provider2 = llm.generate(
                _REVISE_SYSTEM.format(words=COVER_LETTER_WORDS),
                f"""MY PROFILE (the only permitted facts):
{_profile_facts(profile)}

MY SELECTED PROJECTS:
{_format_projects(projects, indices=set(picked))}

THE JOB: {job.get('title', '')} at {job.get('company', '')}
What it asks for:
{reqs_block}

DRAFT TO REVISE:
{draft}

Rewrite it. Output only the final letter.""",
                personal=True,
            )
            revised = _clean_output(revised)
            if len(revised) > 200:          # sanity: not a refusal or a critique
                text, provider = revised, provider2
        except Exception:
            pass                            # draft stands

    text = fill_contact(text, profile)      # identifiers restored, on this machine

    return {
        "text": text,
        "provider": provider,
        "requirements": requirements,
        "projects_used": [projects[i].get("name", "") for i in picked
                          if i < len(projects)],
    }


# ── Resume tailoring ────────────────────────────────────────────────────────

_RESUME_SYSTEM = """You tailor {name}'s resume to a specific job by filling in a
Markdown template. You are a careful editor, not a copywriter.

TEMPLATE IS LAW:
- Reproduce the template EXACTLY: same headings, same order, same Markdown.
- Replace every {{PLACEHOLDER}} with real content. Leave no placeholder behind.
- Do not add sections, do not remove sections, do not reorder them.
- Strip any HTML comment block from the template out of your output.

PLACEHOLDERS — the template contains {{NAME}}, {{EMAIL}}, {{PHONE}}, {{LOCATION}}
and {{LINKS}}. If the profile does not give you values for them, LEAVE THEM AS
THEY ARE — copy the placeholder through to your output untouched. They are filled
in afterwards, on the candidate's own machine. Never invent contact details.
Every OTHER placeholder must be replaced with real content.

TRUTH — this is a resume; a false line is a fireable offence:
- Every fact must come from the profile. Never invent an employer, a date, a job
  title, a metric, a technology, a degree, or a project.
- Never inflate. If the profile says "familiar with AWS", do not write "expert".
- If the job wants something the profile lacks, omit it. Do not imply it.

PLACEHOLDERS — if the profile gives you {{NAME}} instead of a real name, that is
deliberate. Write {{NAME}} exactly as it appears (in the signature, for example).
Do not invent a name, do not write "the candidate", do not leave the signature
blank. It is substituted for the real name after you are done.

OWN WORDS — do not copy and paste:
- The profile is NOTES, not finished resume lines. Rewrite every bullet in clean,
  natural language. Never lift a description verbatim.
- Bullets start with a strong past-tense verb (Built, Designed, Cut, Shipped,
  Automated). Say what was built, with what, and what it achieved.
- Keep bullets to one line each where possible. No fluff, no adjectives like
  "innovative" or "cutting-edge".

TAILORING — same facts, angled at this job:
- Order skills and bullets so the ones this job cares about come first.
- Use the job's own vocabulary where it honestly matches my experience.
- Include only the projects given to you as relevant.

OUTPUT:
- Output ONLY the filled Markdown. No preamble, no explanation, no code fences."""


def generate_resume(job: dict) -> dict:
    """Tailor the resume template to one job.

    Reads config/resume_template.md, fills its placeholders from profile.yaml,
    angled at this specific job. Returns {"text", "provider", "requirements",
    "projects_used"}.
    """
    template_path = CONFIG_DIR / RESUME_TEMPLATE_FILE
    if not template_path.exists():
        raise FileNotFoundError(
            f"Resume template not found at config/{RESUME_TEMPLATE_FILE}. "
            "Add your template there (see the example shipped with JobPilot)."
        )
    template = template_path.read_text(encoding="utf-8")

    profile = load_profile()
    projects = profile.get("projects", []) or []
    real_name = (profile.get("identity", {}) or {}).get("name", "")
    name = "{{NAME}}" if redacting() else real_name

    # Same narrowing as the cover letter: requirements first, then the most
    # relevant of my recent projects.
    requirements = extract_requirements(job)
    picked = select_relevant_projects(
        job, top_n=RESUME_PROJECTS_USED,
        requirements=requirements, pool=RESUME_PROJECT_POOL,
    )

    jd = _strip_html(job.get("description", ""))[:5000]
    reqs_block = "\n".join(f"- {r}" for r in requirements) or "(see description below)"

    system = _RESUME_SYSTEM.format(name=name or "the candidate")
    user = f"""MY PROFILE — the only facts you may use:
{_profile_facts(profile)}

MY PROJECTS TO INCLUDE (these were selected as most relevant; use only these):
{_format_projects(projects, indices=set(picked))}

THE JOB
Role: {job.get('title', '')}
Company: {job.get('company', '')}

What this job asks for:
{reqs_block}

Full description (context):
{jd}

THE TEMPLATE TO FILL (reproduce its structure exactly):
---
{template}
---

Fill the template now. Output only the finished Markdown resume."""

    text, provider = llm.generate(system, user, personal=True)
    text = _clean_output(text)
    text = fill_contact(text, profile)          # identifiers restored, locally

    return {
        "text": text,
        "provider": provider,
        "requirements": requirements,
        "projects_used": [projects[i].get("name", "") for i in picked
                          if i < len(projects)],
    }
