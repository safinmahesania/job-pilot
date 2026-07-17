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
from src.config import load_profile, skill_groups
from src import (resume_limits, resume_guard, resume_fit, resume_schema,
                 resume_select)
from src.paths import (
    COVER_LETTER_WORDS,
    COVER_LETTER_PROJECT_POOL,
    COVER_LETTER_PROJECTS_USED,
    COVER_LETTER_MENTION_RELOCATION,
    COVER_LETTER_REVISE,
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
        # The header line, built by the same code that builds it when nothing is
        # redacted. It used to be assembled here, separately, joining raw URLs with
        # a middle dot — which the renderer splits on "|" and so read as a single
        # unrecognisable blob, and which the link labeller never saw at all.
        "{{CONTACT}}": resume_schema.contact_line(profile)[1],
        "{{LINKS}}": resume_schema.contact_line(profile)[1],
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




def _profile_skill_names(profile: dict) -> list[str]:
    """The profile's own skills as a flat list of names — used to tell the model exactly
    which technologies it may name, so it never reaches for one from the posting that
    isn't ours. Reads both skill shapes (tiered dict and flat list)."""
    skills = profile.get("skills") or {}
    names: list[str] = []
    if isinstance(skills, dict):
        for v in skills.values():
            if isinstance(v, (list, tuple)):
                names.extend(str(s) for s in v)
            elif isinstance(v, str):
                names.append(v)
    elif isinstance(skills, (list, tuple)):
        names.extend(str(s) for s in skills)
    # Also surface technologies named in the projects, since those are real too.
    for p in (profile.get("projects") or []):
        tech = p.get("tech") or p.get("technologies") or p.get("stack") or []
        if isinstance(tech, str):
            names.append(tech)
        elif isinstance(tech, (list, tuple)):
            names.extend(str(t) for t in tech)
    # De-dupe, keep order.
    seen, out = set(), []
    for n in names:
        n = n.strip()
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


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
        lines.append(f"{group['label']}: "
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

def _score_project_against_job(project: dict, job_text: str) -> int:
    """A deterministic relevance score: how many of the project's own words (its name,
    tech, and description) appear in the job text. No model call, so the same job and
    profile always rank the projects the same way — which is what lets the resume and
    the cover letter feature the *same* projects instead of two independent LLM guesses.
    """
    job_low = job_text.lower()
    parts = [project.get("name", ""), project.get("description", "")]
    tech = project.get("tech") or project.get("technologies") or project.get("stack") or []
    if isinstance(tech, str):
        parts.append(tech)
    elif isinstance(tech, (list, tuple)):
        parts.extend(str(t) for t in tech)
    words = {w for w in re.split(r"\W+", " ".join(parts).lower()) if len(w) > 2}
    return sum(1 for w in words if w in job_low)


def select_relevant_projects(job: dict,
                             top_n: int = COVER_LETTER_PROJECTS_USED,
                             requirements: list[str] | None = None,
                             pool: int = COVER_LETTER_PROJECT_POOL) -> list[int]:
    """Rank my REAL projects against the job and return their indices.

    Only the `pool` most recent projects are considered — profile.yaml lists projects
    newest-first, so older work never crowds out current work. Of those, the `top_n`
    most relevant are returned. Nothing is invented; only indices that already exist.

    The ranking is deterministic (keyword overlap between each project and the job), so
    the same job always picks the same projects. That consistency is deliberate: the
    resume and the cover letter both call this, and they must feature the same projects
    rather than two different LLM guesses for one application.
    """
    profile = load_profile()
    projects = profile.get("projects", []) or []
    if not projects:
        return []

    pool_idx = list(range(min(pool, len(projects))))   # the latest N
    if len(pool_idx) <= top_n:
        return pool_idx

    job_text = " ".join([
        job.get("title", ""), job.get("company", ""),
        "\n".join(requirements or []),
        _strip_html(job.get("description", ""))[:2500],
    ])

    # Deterministic rank: score each pooled project, most relevant first. Ties keep
    # profile order (newest first) because the sort is stable.
    ranked = sorted(pool_idx, key=lambda i: _score_project_against_job(projects[i], job_text),
                    reverse=True)
    return ranked[:top_n]


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


def generate_cover_letter(job: dict, fast: bool = False) -> dict:
    """Produce a grounded, first-person cover letter for one job.

    `job` needs: title, company, description.
    `fast` skips the revise pass — one model call instead of two — to stay under a
    proxy's request timeout (a Cloudflare Tunnel cuts at ~100s, which two slow local
    calls can breach, surfacing as a 524).
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

    # The single biggest source of false refusals: the model, told to "name the specific
    # technologies", reaches into the JOB POSTING and names ones we don't have (React,
    # AWS, PostgreSQL…). Give it the explicit, closed list of OUR technologies and forbid
    # any other. This fixes the problem at the source — the letter comes out clean the
    # first time — instead of refusing it after the fact.
    my_tech = _profile_skill_names(profile)
    tech_rule = ""
    if my_tech:
        tech_rule = (
            "\nTECHNOLOGIES I MAY NAME (this list is exhaustive — name ONLY these, and "
            "never a technology from the job posting that is not on this list, even if "
            "the job asks for it):\n"
            + ", ".join(my_tech)
            + "\nIf the job wants something I don't have, write about transferable "
            "experience in general terms — never claim the specific tool.\n"
        )

    structure = _STRUCTURE.format(
        name=name,
        relocation=_RELOCATION_LINE if COVER_LETTER_MENTION_RELOCATION else "",
    )

    system = _VOICE_RULES.format(name=name or "the applicant",
                                 words=COVER_LETTER_WORDS)
    user = f"""MY PROFILE — the only facts I may use:
{_profile_facts(profile)}
{tech_rule}
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

    # Step 4: critique + rewrite. A second full generation, so it roughly doubles the
    # time. Behind a proxy with a request timeout (Cloudflare Tunnel cuts at ~100s), two
    # slow local-model calls can exceed the limit and surface as a 524. `fast` skips it —
    # the draft is already grounded and guarded; the revise only polishes.
    text = draft
    if COVER_LETTER_REVISE and not fast:
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

    # The last gate, and the reason this is safe to send. The resume cannot fabricate
    # because it only selects; the cover letter writes prose, so it is checked the way
    # the resume summary is: every technology it names must be one you actually have.
    # A letter that claims a framework from the job description you have never used is
    # not returned — the same fatal stance as an invented employer on a resume, since
    # a cover letter goes to a named human who can check.
    problems = resume_guard.check_cover_letter_prose(
        text, profile, target_company=job.get("company", ""),
        job_description=job.get("description", ""))

    # A cover letter should never be refused outright — that leaves you with nothing.
    # If the draft named a technology from the posting that isn't in your profile, try
    # ONCE to regenerate with those names explicitly forbidden. If it still slips one in,
    # return the letter anyway with a non-blocking note, so you can edit it here rather
    # than being handed a wall of red. The guard's job is to warn, not to withhold.
    warnings: list[str] = []
    if problems:
        bad = resume_guard.fabricated_terms(
            text, profile, target_company=job.get("company", ""),
            job_description=job.get("description", ""))
        if bad:
            retry_user = user + (
                "\n\nIMPORTANT: your previous draft named these technologies, which I do "
                "NOT have and must NOT appear anywhere in the letter: "
                + ", ".join(bad)
                + ". Rewrite the letter without naming any of them — write about "
                "transferable experience in general terms instead."
            )
            try:
                retry, provider_r = llm.generate(system, retry_user, personal=True)
                retry = _clean_output(retry)
                if len(retry) > 200:
                    still = resume_guard.fabricated_terms(
                        retry, profile, target_company=job.get("company", ""),
                        job_description=job.get("description", ""))
                    text, provider = retry, provider_r
                    if still:
                        warnings.append(
                            "This letter still mentions "
                            + ", ".join(still)
                            + ", which aren't in your profile. Edit those out before "
                            "sending, or add them to your profile if you do have them."
                        )
                else:
                    warnings.append(
                        "Heads up: this letter mentions "
                        + ", ".join(bad)
                        + ", which aren't in your profile. Edit them out or add them "
                        "to your profile."
                    )
            except Exception:
                warnings.append(
                    "Heads up: this letter mentions "
                    + ", ".join(bad)
                    + ", which aren't in your profile. Edit them out or add them to "
                    "your profile."
                )

    text = fill_contact(text, profile)      # identifiers restored, on this machine

    return {
        "text": text,
        "provider": provider,
        "requirements": requirements,
        "projects_used": [projects[i].get("name", "") for i in picked
                          if i < len(projects)],
        "warnings": warnings,
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


def _format_projects(projects: list, indices: set | None = None) -> str:
    """The projects, as notes for the model.

    Still used by the cover letter, which — unlike the resume — genuinely does write
    prose, because a cover letter is prose. The resume no longer calls this: it
    selects project indices and the code renders them from the profile.
    """
    if not projects:
        return "(no projects listed)"

    out = []
    for i, p in enumerate(projects):
        if indices is not None and i not in indices:
            continue

        tech = ", ".join(str(t) for t in (p.get("tech") or []))
        bits = [f"[{i}] {p.get('name', '')}"]
        if p.get("owner"):
            bits.append(f"\n    OWNER: {p['owner']}")
        if tech:
            bits.append(f"\n    TECH: {tech}")
        if p.get("link"):
            bits.append(f"\n    LINK: {p['link']}")
        if p.get("description"):
            bits.append(f"\n    WHAT IT IS: {p['description']}")

        line = "".join(bits)
        for h in p.get("highlights", []) or []:
            line += f"\n    - {h}"
        out.append(line)

    return "\n".join(out) if out else "(no projects listed)"


def generate_resume(job: dict) -> dict:
    """Select from the profile what this job should see. Do not write it.

    The model returns numbers — which jobs in which order, which of their bullets,
    which projects, which skills first — and the code assembles the page from the
    profile using them. A bullet on the finished resume is a bullet from
    profile.yaml, character for character, because there is no step at which it
    could become anything else.

    The summary is the one exception, and the only place the model still writes. It
    is also, now, the only place a lie can enter, which is why it is the only thing
    still checked.
    """
    profile = load_profile()

    missing = resume_guard.validate_profile(profile)
    if missing:
        raise resume_guard.ProfileIncompleteError(missing)

    real_name = (profile.get("identity", {}) or {}).get("name", "")
    redacted = redacting()
    name = "{{NAME}}" if redacted else real_name

    requirements = extract_requirements(job)

    score, matched = resume_fit.overlap(requirements, profile, job)
    detail = f" ({', '.join(sorted(matched)[:6])})" if matched else ""
    print(f"  fit: {score:.0%} of what this job asks for is in your profile{detail}")
    resume_fit.check_fit(job, requirements, profile)

    jd = _strip_html(job.get("description", ""))[:5000]
    reqs_block = "\n".join(f"- {r}" for r in requirements) or "(see description)"

    system = _RESUME_SYSTEM.format(name=name or "the candidate",
                                   limits=resume_limits.instructions(profile))

    user = f"""THE JOB I AM APPLYING TO — this is CONTEXT, not content.
It tells you which of my real experiences to lead with. It does not tell you who I
am, where I worked, or what I know.

Role: {job.get('title', '')}
Company: {job.get('company', '')}

What it asks for:
{reqs_block}

Full description:
{jd}

────────────────────────────────────────────────────────────────────────
Everything below is ME. Everything above is the job. Nothing crosses over.
────────────────────────────────────────────────────────────────────────

{resume_select.choices(profile)}

MY OWN SUMMARY, which yours must be built from:
{profile.get('summary', '')}

────────────────────────────────────────────────────────────────────────

RETURN THIS JSON. Numbers, and one paragraph:

{resume_select.shape_for_prompt(profile)}

Output ONLY the JSON. No prose, no markdown fences."""

    selection, provider = _select(system, user, profile)
    resume = resume_select.resolve(selection, profile)

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
        "projects_used": [p["name"] for p in resume["projects"]],
    }


def _select(system: str, user: str, profile: dict,
            attempts: int = 3) -> tuple[dict, str]:
    """Ask for the selection, and check the one thing that can still be a lie.

    The numbers cannot lie. An index the model invents is simply out of range and
    disappears; an index it omits is filled in from the profile. There is nothing
    to guard, so there is nothing here guarding it.

    The summary can lie, because the summary is written. So the summary — and only
    the summary — is read back and checked against the profile, and a model that
    claims React for someone who has never touched React is told so and asked again.
    """
    complaint = ""
    problems: list[str] = []
    best: tuple[dict, str] | None = None

    for attempt in range(attempts):
        prompt = user if not complaint else (
            f"{user}\n\n"
            f"YOUR PREVIOUS SUMMARY WAS WRONG:\n{complaint}\n\n"
            f"Rewrite the summary using only what is in MY PROFILE above. Keep the "
            f"rest of your selection as it was. Return the corrected JSON."
        )

        text, provider = llm.generate(system, prompt, personal=True)

        try:
            selection = resume_select.parse(text)
        except resume_select.MalformedResume as e:
            complaint = f"- {e}. Return a single JSON object and nothing else."
            problems = [str(e)]
            continue

        summary = {"summary": str(selection.get("summary") or "")}
        problems = resume_guard.check_prose(summary, profile)

        if not problems:
            short = resume_limits.summary_is_short(summary, profile)
            if not short:
                return selection, provider

            best = (selection, provider)
            if attempt == 0:
                complaint = f"- {short}"
                print("  the summary is thinner than your profile supports "
                      "— asking again")
                continue
            return selection, provider

        complaint = "\n".join(f"- {p}" for p in problems)
        print(f"  attempt {attempt + 1}: the summary claims things you do not have:"
              f"\n{complaint}")

    if best:
        print("  a later summary invented things — keeping the honest earlier one")
        return best

    raise resume_guard.FabricationError(problems)
