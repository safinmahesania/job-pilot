"""Application-form autofill: turns profile.yaml into answers a form can use.

The browser extension asks this module two things:

  1. `answers()` — a flat, canonical set of values (name, email, phone, work
     authorisation, relocation…). The extension matches most form fields against
     these with local heuristics, no AI call, instantly.

  2. `resolve()` — for the fields heuristics can't place ("Why do you want to work
     here?", "What is your notice period?", odd dropdowns), the extension sends
     the field's label/type/options here and the LLM maps them to the profile.

The second path is strictly grounded: if the profile has no answer, the model
must return an empty string. It is better to leave a field blank than to invent
an answer that ends up on a real job application.
"""
import json
import re

from src import llm
from src.config import load_profile


# ── Canonical answers (no AI needed) ────────────────────────────────────────

def answers() -> dict:
    """Flatten the profile into the values a job application asks for.

    Keys are canonical names; the extension maps a form field to a key using its
    own heuristics. Empty values are kept (so the extension knows to skip them
    rather than guess).
    """
    p = load_profile()
    ident = p.get("identity", {}) or {}
    contact = p.get("contact", {}) or {}
    app = p.get("application", {}) or {}
    edu = (p.get("education", []) or [{}])[0]
    exp = (p.get("experience", []) or [{}])[0]

    full_name = ident.get("name", "")
    first = ident.get("first_name") or (full_name.split()[0] if full_name else "")
    last = ident.get("last_name") or (
        " ".join(full_name.split()[1:]) if len(full_name.split()) > 1 else ""
    )

    skills = p.get("skills", {}) or {}
    all_skills: list = []
    for tier in ("expert", "proficient", "familiar"):
        all_skills += skills.get(tier, []) or []

    return {
        # identity
        "full_name": full_name,
        "first_name": first,
        "last_name": last,
        # contact
        "email": contact.get("email", ""),
        "phone": contact.get("phone", ""),
        "address": contact.get("address", ""),
        "city": contact.get("city", ""),
        "province": contact.get("province", ""),
        "state": contact.get("province", ""),        # US wording
        "country": contact.get("country", ""),
        "postal_code": contact.get("postal_code", ""),
        "linkedin": contact.get("linkedin", ""),
        "github": contact.get("github", ""),
        "website": contact.get("website", ""),
        # eligibility
        "work_authorized": app.get("work_authorized", ""),
        "needs_sponsorship": app.get("needs_sponsorship", ""),
        "requires_visa": app.get("requires_visa", ""),
        "willing_to_relocate": app.get("willing_to_relocate", ""),
        # work arrangement — hybrid/onsite commitment questions
        "work_arrangement": app.get("work_arrangement", ""),
        "willing_to_work_onsite": app.get("willing_to_work_onsite", ""),
        "max_days_onsite_per_week": app.get("max_days_onsite_per_week", ""),
        "willing_to_commute": app.get("willing_to_commute", ""),
        "commute_locations": ", ".join(app.get("commute_locations", []) or []),
        # logistics
        "notice_period": app.get("notice_period", ""),
        "salary_expectation": app.get("salary_expectation", ""),
        "years_of_experience": app.get("years_of_experience", ""),
        "how_did_you_hear": app.get("how_did_you_hear", ""),
        # background
        "current_company": exp.get("company", ""),
        "current_title": exp.get("role", ""),
        "school": edu.get("institution", ""),
        "degree": edu.get("degree", ""),
        "field_of_study": edu.get("field", ""),
        "graduation_year": str(edu.get("end", "") or ""),
        "skills": ", ".join(all_skills),
        # voluntary — blank means "do not answer"
        "gender": app.get("gender", ""),
        "ethnicity": app.get("ethnicity", ""),
        "veteran_status": app.get("veteran_status", ""),
        "disability_status": app.get("disability_status", ""),
    }


def custom_answers() -> list[dict]:
    """Your own fixed answers to recurring questions.

    Each entry is {"match": [keywords], "answer": "..."}. The extension checks
    these before calling the AI: if every keyword appears in the field's label,
    that answer is used verbatim. Entries with a blank answer are ignored, which
    lets you leave a question to the AI while keeping it documented.
    """
    p = load_profile()
    out = []
    for rule in p.get("custom_answers", []) or []:
        keywords = [str(k).strip().lower() for k in (rule.get("match") or []) if str(k).strip()]
        answer = rule.get("answer", "")
        if keywords and str(answer).strip():
            out.append({"match": keywords, "answer": str(answer)})
    return out


# ── AI fallback for fields the heuristics can't place ───────────────────────

_RESOLVE_SYSTEM = """You map job-application form fields to a candidate's real
answers. You are filling in a form that a human will actually submit, so a wrong
answer is worse than no answer.

RULES:
- Use ONLY the profile given to you. Never invent a fact.
- If the profile does not answer a field, return an empty string for it. Blank is
  always acceptable. Guessing is not.
- For a field with options, return EXACTLY one of the given option strings — copy
  it character for character. If none of the options fit the profile, return "".
- For yes/no fields, return exactly "Yes" or "No" (or the matching option string).

COMMITMENT AND LOGISTICS QUESTIONS — reason, do not guess:
- Employers ask about office attendance in many shapes: "can you commit to being
  in-office three days per week?", "this role is onsite 5 days — are you able to
  comply?", "are you able to commute to our Toronto office?". Answer them by
  comparing what is ASKED against what the profile ALLOWS:
    * application.max_days_onsite_per_week = the most days the candidate will
      commit to. If the job asks for that number or fewer, answer "Yes". If it
      asks for more, answer "No".
    * application.willing_to_work_onsite = false means "No" to any onsite or
      hybrid commitment question.
    * application.willing_to_commute and application.commute_locations tell you
      whether a specific office is reachable. If the office named in the question
      is in commute_locations, or the candidate is willing to relocate, "Yes".
    * If the question names no number and the candidate is willing to work onsite,
      answer "Yes".
- Never over-promise. If the profile does not support a commitment, answer "No"
  rather than "Yes" — a broken commitment is worse than a rejected application.
- For free-text questions (e.g. "why do you want to work here"), write 2-3 honest
  sentences in the first person, grounded only in the profile. No clichés.
- Never answer voluntary demographic questions (gender, race, veteran status,
  disability) unless the profile explicitly provides a value.

Return ONLY a JSON object mapping each field's "id" to its answer string.
No prose, no markdown, no explanation."""


def _is_voluntary(label: str) -> bool:
    """Is this an optional demographic question?

    Race, gender, disability and veteran status are voluntary by law, and the
    decision to disclose is yours — not a tool's, and certainly not a language
    model's. Once it's submitted you can't take it back.

    The extension already declines to send these. This is the second lock: the
    backend does not trust its client, and it does not trust the model to obey an
    instruction in a prompt. A field matching these patterns is blanked no matter
    what comes back.
    """
    text = (label or "").lower()
    return bool(_VOLUNTARY_PATTERN.search(text))


_VOLUNTARY_PATTERN = re.compile(
    r"\b(gender|ethnicity|race|racial|hispanic|latino|veteran|disability|"
    r"disabled|sexual orientation|transgender|pronoun|"
    r"self[- ]identif\w*|demographic)\b",
    re.I,
)


def _custom_answer_for(label: str, rules: list[dict]) -> str | None:
    """Your own fixed answer, if one matches. Checked before the model is asked —
    a question you have already settled should never be re-invented."""
    text = (label or "").lower()
    for rule in rules:
        if all(keyword in text for keyword in rule["match"]):
            return rule["answer"]
    return None


def resolve(fields: list[dict], job: dict | None = None) -> dict:
    """Map unknown form fields to grounded answers.

    `fields` is a list of descriptors from the extension:
        {"id": "...", "label": "...", "type": "text|select|radio|textarea",
         "options": ["...", "..."]}

    Returns {field_id: answer}. Fields with no honest answer map to "".
    """
    if not fields:
        return {}

    resolved: dict[str, str] = {}
    to_ask: list[dict] = []
    rules = custom_answers()

    for field in fields:
        label = field.get("label", "")

        # Never, under any circumstances, and without asking a model.
        if _is_voluntary(label):
            continue

        # Already answered by you, once, on purpose.
        answer = _custom_answer_for(label, rules)
        if answer is not None:
            resolved[field.get("id")] = answer
            continue

        to_ask.append(field)

    if not to_ask:
        return resolved

    resolved.update(_resolve_with_ai(to_ask, job))
    return resolved


def _resolve_with_ai(fields: list[dict], job: dict | None = None) -> dict:
    """The AI fallback, for fields no rule and no saved answer could place."""
    p = load_profile()
    ident = p.get("identity", {}) or {}
    skills = p.get("skills", {}) or {}

    # Compact profile view — enough to answer the questions the local rules
    # couldn't, and nothing more.
    #
    # Contact details are deliberately absent. Name, email, phone and address are
    # matched by the extension's own rules, locally, without an AI call — so there
    # is no reason to put them in a prompt, and they are not put in one. What the
    # model gets is the work history it needs to answer "why do you want to work
    # here" and "how many years of Python", and nothing that identifies you
    # directly.
    profile_view = {
        "seniority": ident.get("seniority", ""),
        "application": p.get("application", {}),
        "summary": p.get("summary", ""),
        "skills": skills,
        "experience": p.get("experience", []),
        "education": p.get("education", []),
        "projects": [
            {k: v for k, v in proj.items() if k in ("name", "description", "tech")}
            for proj in (p.get("projects", []) or [])[:4]
        ],
    }
    if llm.privacy_mode() == "full":
        profile_view["identity"] = ident
        profile_view["contact"] = p.get("contact", {})

    job_ctx = ""
    if job:
        job_ctx = (f"\nTHE JOB BEING APPLIED TO:\n"
                   f"Role: {job.get('title', '')}\n"
                   f"Company: {job.get('company', '')}\n")

    user = (
        f"MY PROFILE (the only permitted source of facts):\n"
        f"{json.dumps(profile_view, indent=2, default=str)}\n"
        f"{job_ctx}\n"
        f"FORM FIELDS TO ANSWER:\n"
        f"{json.dumps(fields, indent=2)}\n\n"
        f'Return a JSON object like {{"field_id": "answer", ...}}. '
        f'Use "" for anything the profile does not answer.'
    )

    # personal=True: this prompt contains your name, address, phone and the
    # answers about to go on a real application. It never leaves the machine.
    text, _ = llm.generate(_RESOLVE_SYSTEM, user, personal=True)

    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}

    # Only answers to fields we actually asked about, as strings — and a final
    # pass on voluntary questions. The model was told not to answer them; this is
    # what makes it true rather than merely requested.
    wanted = {f.get("id"): f.get("label", "") for f in fields}
    return {
        k: ("" if v is None else str(v))
        for k, v in data.items()
        if k in wanted and not _is_voluntary(wanted[k])
    }
