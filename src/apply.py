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
from src import resume_limits, resume_guard, resume_fit, resume_schema
from src.llm import LLMError
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


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _month_year(value) -> str:
    """"2024-05" -> "May 2024". Anything else is passed through untouched.

    The profile stores dates sortably; a resume wants them readable. Doing the
    conversion here rather than asking the model to do it removes one more thing
    it can quietly get wrong — and a wrong date on a resume is a real problem.
    """
    if not value:
        return ""
    text = str(value).strip()
    if text.lower() in ("present", "current", "now"):
        return "Present"
    parts = text.split("-")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        month = int(parts[1])
        if 1 <= month <= 12:
            return f"{_MONTHS[month - 1]} {parts[0]}"
    return text


def _span(start, end) -> str:
    """"Sep 2024 - Apr 2026", or just one side if that's all there is."""
    a, b = _month_year(start), _month_year(end)
    if a and b:
        return f"{a} - {b}"
    return a or b


def skill_groups(profile: dict) -> list[dict]:
    """The resume's skill lines: [{"label": ..., "skills": [...]}, ...].

    The profile shape is a list, so the order is yours and the labels are yours:

        skill_categories:
          - label: Programming Skills
            skills: [Dart, Java, C, ...]

    A dict is still accepted, for profiles written before this changed. Either
    way, an empty category is dropped — an empty "Deep Learning:" line on a resume
    reads as something you failed to fill in.
    """
    raw = profile.get("skill_categories") or []
    groups = []

    if isinstance(raw, dict):
        # Older shape: fixed keys, labels supplied here.
        legacy = {
            "programming": "Programming & Core Concepts",
            "frameworks": "Frameworks & Development",
            "databases": "Databases",
            "cloud": "Cloud & Distributed Systems",
            "ml": "Machine Learning & AI",
            "tools": "Developer Tools & Environments",
            "methods": "Methodologies & Practices",
            "languages": "Languages",
        }
        for key, label in legacy.items():
            skills = raw.get(key) or []
            if skills:
                groups.append({"label": label,
                               "skills": [str(s) for s in skills]})
        return groups

    for entry in raw:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label", "")).strip()
        skills = [str(s).strip() for s in (entry.get("skills") or [])
                  if str(s).strip()]
        if label and skills:
            groups.append({"label": label, "skills": skills})
    return groups


def _ordinal(n: int) -> str:
    """"a 4th", "a 3rd" — not "a 3th"."""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"a {n}{suffix}"


def closed_lists(profile: dict) -> str:
    """The exact, complete, numbered set of proper nouns the resume may contain.

    "Never invent an employer" is an open-ended prohibition, and a model asked to
    tailor a resume to a job that wants more experience than you have will quietly
    add one — not out of malice, but because the instruction says what NOT to do
    without ever saying what the complete truth IS. It never learns that the list
    is finished.

    Counting them closes the world. "You have exactly three employers, here they
    are, add none" turns an open generative task into a fill-in-the-blanks one, and
    a model that would happily invent a fourth employer will not invent a fourth
    item in a list it has been told has three.
    """
    def block(label, values, noun):
        if not values:
            return (f"{label}: you have NONE. The resume must not contain a "
                    f"{noun} section at all.")
        lines = [f"{label}: you have EXACTLY {len(values)}. "
                 f"These and no others:"]
        lines += [f"  {i + 1}. {v}" for i, v in enumerate(values)]
        lines.append(f"  Do not add {_ordinal(len(values) + 1)}. There isn't one.")
        return "\n".join(lines)

    employers = [e.get("company", "") for e in (profile.get("experience") or [])
                 if e.get("company")]
    schools = [e.get("institution", "") for e in (profile.get("education") or [])
               if e.get("institution")]
    certificates = [c.get("name", "") for c in (profile.get("certificates") or [])
                    if c.get("name")]
    organisations = [
        f"{v.get('organization', '')} ({v.get('role', '')})"
        for v in (profile.get("volunteer") or []) if v.get("organization")
    ]

    return "\n\n".join([
        block("EMPLOYERS", employers, "Work Experience"),
        block("SCHOOLS", schools, "Education"),
        block("CERTIFICATES", certificates, "Certificates and Achievements"),
        block("VOLUNTEER ORGANISATIONS", organisations,
              "Volunteer and Community Involvement"),
    ])


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

    # The resume's skill groupings — labels, order and contents all come from the
    # profile. They used to be hardcoded here, with names I picked, which meant
    # anyone whose resume grouped skills differently had to bend to fit. It is
    # their resume; these are their headings.
    #
    # A category with nothing in it is not listed, so the model is never handed an
    # empty line it might feel obliged to fill.
    for group in skill_groups(profile):
        lines.append(f"Skill category — {group['label']}: "
                     f"{' | '.join(group['skills'])}")

    for exp in profile.get("experience", []) or []:
        where = f", {exp['location']}" if exp.get("location") else ""
        lines.append(
            f"Experience: {exp.get('role','')} at {exp.get('company','')}{where} "
            f"({_span(exp.get('start'), exp.get('end'))})".strip()
        )
        for h in exp.get("highlights", []) or []:
            lines.append(f"  - {h}")

    for edu in profile.get("education", []) or []:
        where = f", {edu['location']}" if edu.get("location") else ""
        gpa = f", GPA {edu['gpa']}" if edu.get("gpa") else ""
        lines.append(
            f"Education: {edu.get('degree','')} in {edu.get('field','')}, "
            f"{edu.get('institution','')}{where} "
            f"({_span(edu.get('start'), edu.get('end'))}){gpa}".strip()
        )

    for cert in profile.get("certificates", []) or []:
        link = f" ({cert['link']})" if cert.get("link") else ""
        when = f" — {cert['date']}" if cert.get("date") else ""
        lines.append(f"Certificate: {cert.get('name','')}{when}{link}")

    for vol in profile.get("volunteer", []) or []:
        lines.append(
            f"Volunteer: {vol.get('organization','')} / {vol.get('role','')} — "
            f"{vol.get('description','').strip()}"
        )

    return "\n".join(lines)


def _format_projects(projects: list, indices=None) -> str:
    """Render projects (optionally only the selected indices) as numbered lines."""
    out = []
    for i, p in enumerate(projects):
        if indices is not None and i not in indices:
            continue
        # Labelled fields, one per line. The old one-line format —
        #   [0] Plant Disease Detection (owner: course) — ... (tech: ...) (link: ...)
        # was copied onto the resume verbatim, "(owner: course)" and all. A model
        # given a line that looks like output will treat it as output. These are
        # notes, so they must look like notes.
        tech = ", ".join(p.get("tech", []) or [])
        bits = [f"[{i}] {p.get('name', '')}"]
        if p.get("owner"):
            bits.append(f"\n    OWNER: {p['owner']}   (write this after the "
                        f"project name, e.g. \"Plant Disease Detection - "
                        f"{p['owner'].title()}\")")
        if tech:
            bits.append(f"\n    TECH: {tech}")
        if p.get("link"):
            bits.append(f"\n    LINK: {p['link']}   (goes on the right, after @@)")
        if p.get("description"):
            bits.append(f"\n    WHAT IT IS: {p['description']}")
        line = "".join(bits)
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

_RESUME_SYSTEM = """You SELECT from {name}'s real background. You do not tailor
them to a job.

That distinction is the whole task, so be clear about it: "tailor this resume to
this job" invites you to become whoever the job is looking for, and a model that
takes the invitation writes a sales resume for a software engineer — fluently,
plausibly, and entirely falsely. That has happened. It is what this instruction
exists to prevent.

The job decides ORDER and EMPHASIS and VOCABULARY. It never decides CONTENT.
  - ORDER: lead with the experience this employer cares about most.
  - EMPHASIS: give it more words; give the rest fewer.
  - VOCABULARY: where the job says "REST APIs" and the profile says "RESTful
    services", use the job's phrase — it is the same thing.
  - CONTENT: comes ONLY from the profile. Always. Without exception.

If the job asks for something {name} does not have, {name} does not have it. Do
not claim it. Not claiming a skill you lack is honest; inventing one is not.

BUT — and a previous attempt got this exactly backwards — that applies to CLAIMS,
never to FACTS. Told not to invent, it deleted {name}'s entire work history and
wrote "(No work experience listed)" for someone with three real jobs. It had
decided the jobs weren't relevant to the posting, so it left them out.

That is not honesty. That is a different lie.

EVERY section of the template that the profile has content for MUST be filled, in
full:
  - Three employers in the profile means THREE entries under Work Experience.
  - Two schools means TWO under Education. Two certificates means two.
  - A job that does not value your experience does not erase your experience.

A teaching assistant's job is still a job when you apply to a bank. A banking
support role is still a role when you apply to a startup. You may choose which of
their bullets to lead with. You may not choose whether the job existed.

Nothing real is ever dropped. Nothing false is ever added. Those are two separate
rules and you must obey both — satisfying one by breaking the other is not a
compromise, it is a failure.

You are a careful editor, not a copywriter.

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

{{SKILLS}} — one bullet per category, in the order the profile lists them, with
the category name in bold exactly as the profile writes it:

    - **Programming Skills:** Dart | Java | C | C# | Python
    - **Databases:** MySQL | SQL Server | Cloud Firestore | SQLite

Order the skills WITHIN each line so the ones this job cares about come first.
Never add a skill that is not in the profile, and never rename a category.

THE `@@` CONVENTION — this is how dates land on the right:
`@@` splits a line into left and right. Everything after it is pushed to the right
margin when the resume is rendered. Use it exactly as the template shows:

    ### Software Developer Intern @@ May 2024 - Aug 2024
    Acme Corp, Toronto, ON
    - Cut API latency 40% by putting a Redis cache in front of the pricing service.

    ### Master of Science, Computer Science @@ Sep 2024 - Apr 2026
    Concordia University, Montreal, QC

    ### JobPilot - Personal (Python, FastAPI) @@ github.com/you/job-pilot
    - Built a pipeline that fetches 70+ boards concurrently and scores each posting.

    - AWS Certified Cloud Practitioner @@ credly.com/badges/abc

One `@@` per line at most. No `@@` on a line that has nothing to put on the right.

EMPTY SECTIONS — drop them:
If the profile has no certificates, remove the "## Certificates and Achievements"
heading entirely, along with its placeholder. Same for Volunteer, and for any
skills category with nothing in it. An empty heading on a resume is worse than no
heading — it reads as something you failed to fill in.

{limits}

OUTPUT:
- Output ONLY the filled Markdown. No preamble, no explanation, no code fences."""


def generate_resume(job: dict) -> dict:
    """Select from the profile what this job should see, and render it.

    The model fills in fields. It does not write a document. It never sees a `###`
    or an `@@`, so it cannot get them wrong — and the checks that follow read a
    structure that is given, rather than one inferred back out of prose. Inferring
    it is what used to refuse honest resumes for writing a heading in bold instead
    of with three hashes.
    """
    profile = load_profile()

    missing = resume_guard.validate_profile(profile)
    if missing:
        raise resume_guard.ProfileIncompleteError(missing)

    projects = profile.get("projects", []) or []
    real_name = (profile.get("identity", {}) or {}).get("name", "")
    redacted = redacting()
    name = "{{NAME}}" if redacted else real_name

    requirements = extract_requirements(job)

    score, matched = resume_fit.overlap(requirements, profile, job)
    detail = f" ({', '.join(sorted(matched)[:6])})" if matched else ""
    print(f"  fit: {score:.0%} of what this job asks for is in your profile{detail}")
    resume_fit.check_fit(job, requirements, profile)

    picked = select_relevant_projects(
        job, top_n=RESUME_PROJECTS_USED,
        requirements=requirements, pool=RESUME_PROJECT_POOL,
    )

    jd = _strip_html(job.get("description", ""))[:5000]
    reqs_block = "\n".join(f"- {r}" for r in requirements) or "(see description)"

    system = _RESUME_SYSTEM.format(name=name or "the candidate",
                                   limits=resume_limits.instructions())

    # The job first, as context. My real background last, closest to the pen.
    # Recency does the rest: a model steeped in a posting and then told to write
    # writes the posting.
    user = f"""THE JOB I AM APPLYING TO — this is CONTEXT, not content.
It tells you which of my real experiences to lead with and which words to use for
them. It does not tell you who I am, where I worked, or what I know.

Role: {job.get('title', '')}
Company: {job.get('company', '')}

What it asks for:
{reqs_block}

Full description:
{jd}

────────────────────────────────────────────────────────────────────────
Everything below is ME. Everything above is the job. Nothing crosses over.
────────────────────────────────────────────────────────────────────────

THE COMPLETE LISTS — closed. Nothing may be added to them.

{closed_lists(profile)}

MY PROJECTS TO INCLUDE (these were selected as most relevant; use only these):
{_format_projects(projects, indices=set(picked))}

MY PROFILE — the only facts you may use:
{_profile_facts(profile)}

RETURN THIS EXACT JSON SHAPE, filled in from MY PROFILE above:

{resume_schema.shape_for_prompt()}

Every key is required. A list is empty ONLY if my profile has nothing for it —
NEVER because you judged it irrelevant to this job.

Output ONLY the JSON. No prose, no markdown fences."""

    resume, provider = _generate_structured(system, user, profile)

    text = resume_schema.to_markdown(resume, profile, name, redacted=redacted)
    if redacted:
        text = fill_contact(text, profile)

    overruns = resume_limits.check_structured(resume)

    return {
        "overruns": [
            {"where": o.where, "allowed": o.allowed, "actual": o.actual}
            for o in overruns
        ],
        "text": text,
        "provider": provider,
        "requirements": requirements,
        "projects_used": [projects[i].get("name", "") for i in picked
                          if i < len(projects)],
    }


def _generate_structured(system: str, user: str, profile: dict,
                         attempts: int = 3) -> tuple[dict, str]:
    """Ask for the JSON, verify it, and say exactly what was wrong if it is not.

    Three attempts. A model that invents an employer on the first pass usually
    stops once it is told precisely which one and that the list is closed — what it
    cannot do is guess what it got wrong, and it was never given a reason to.
    """
    complaint = ""
    problems: list[str] = []

    for attempt in range(attempts):
        prompt = user if not complaint else (
            f"{user}\n\n"
            f"YOUR PREVIOUS ATTEMPT WAS WRONG:\n{complaint}\n\n"
            f"Fix ONLY what is listed. Do NOT delete a section to make a problem go "
            f"away — every real entry stays, every invented one goes. Return the "
            f"corrected JSON."
        )

        text, provider = llm.generate(system, prompt, personal=True)

        try:
            resume = resume_schema.parse(text)
        except resume_schema.MalformedResume as e:
            complaint = f"- {e}. Return a single JSON object and nothing else."
            problems = [str(e)]
            continue

        problems = resume_guard.check_structured(resume, profile)
        if not problems:
            return resume, provider

        complaint = "\n".join(f"- {p}" for p in problems)
        print(f"  attempt {attempt + 1} had problems:\n{complaint}")

    raise resume_guard.FabricationError(problems)
