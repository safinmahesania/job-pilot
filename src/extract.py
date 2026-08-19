"""Pull the structured fields out of a job's free-text description.

A posting's description is one blob of HTML: buried in it are the work mode, the
requirements, the responsibilities, the benefits, the "about us" — the things a
reader scans for and a filter would want. This runs ONE LLM call per job to lift
those out into named fields, so the UI can show them as sections and the feed can
filter on them.

This is deliberately separate from scoring (src/scoring/rerank.py):

  - Scoring judges fit against YOUR profile and changes when the profile changes.
    Extraction reads only the posting and never changes once done — so re-scoring
    must not re-extract, and importing a job (which doesn't score) still can.
  - The scoring prompt is personal (carries your background); this one is not —
    a job description is public text, so it uses the normal provider chain with
    personal=False and raises no privacy question.

The model returns strings. A field the posting genuinely doesn't mention comes
back as "" (empty), NOT a guess — the same rule scoring follows: better an honest
blank than an invented value a filter would trust.
"""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

from src import llm
from src.normalize import strip_html

# A description shorter than this has nothing worth a model call — a snippet, a
# "login to view", a stub. Extraction is skipped and the fields stay NULL.
MIN_DESCRIPTION_CHARS = 120

# The model reads this much of the body. Long enough for the sections that matter,
# short enough to keep the call cheap; mirrors the scoring prompt's own cap.
MAX_DESCRIPTION_CHARS = 6000

# Constrain the free-text enums so the UI can rely on them (colour a chip, filter
# a column). Anything the model returns outside the set is normalised to "".
WORK_MODES = {"remote", "hybrid", "onsite"}
SENIORITY_LEVELS = {"intern", "junior", "mid", "senior", "lead"}


class Extraction(BaseModel):
    """Every field is a string; "" means the posting didn't say."""
    work_mode: str = ""
    seniority_level: str = ""
    location_detail: str = ""
    salary_text: str = ""
    benefits: str = ""
    responsibilities: str = ""
    requirements: str = ""
    nice_to_have: str = ""
    tech_stack: str = ""
    about_company: str = ""
    instructions: str = ""


# The fields extraction owns, in one place — used by the store to write them, by
# the backfill to know what to update, and by is_extracted() to check a row.
FIELDS = tuple(Extraction.model_fields.keys())


_SYSTEM = (
    "You read one job posting and pull out its parts. You are precise and you never "
    "invent: if the posting does not state something, you return an empty string for "
    "that field rather than guessing. You copy the posting's own wording; you do not "
    "summarise or embellish."
)


def _prompt(job: dict, body: str) -> str:
    return f"""From the JOB POSTING below, extract these fields. If the posting does
not mention a field, return "" for it — do NOT guess or fill from the title alone.

- work_mode: exactly one of "remote", "hybrid", "onsite", or "" if unclear. Do not
  infer from the city alone — a location is not a work mode.
- seniority_level: one of "intern", "junior", "mid", "senior", "lead", or "".
  Read the posting's own signals: an internship, co-op, or "stage" (in ANY
  language, e.g. French "stage"/"stagiaire") is "intern". A stated level word wins
  ("Senior" -> senior, "Junior" -> junior, "Lead"/"Principal"/"Staff" -> lead).
  Otherwise infer from the required YEARS of experience: 0-2 -> "junior",
  3-5 -> "mid", 6-9 -> "senior", 10+ -> "lead". If the posting names no level and
  no years, or spans several at once ("junior to senior"), return "".
- location_detail: the city/region/country the role is based in, if named.
- salary_text: the pay exactly as written ("$80,000–$100,000 + equity"), or "".
- benefits: perks and benefits — health, PTO, remote stipend, etc. — as a short list.
- responsibilities: what the person will do day to day (the duties / "what you'll do").
- requirements: the must-haves — required skills, YEARS OF EXPERIENCE, and EDUCATION
  all belong here, combined.
- nice_to_have: preferred or bonus qualifications, kept separate from requirements.
- tech_stack: the specific technologies named ANYWHERE in the posting — languages,
  frameworks, libraries, databases, cloud services, tools — comma-separated, names
  only. Collect them from a dedicated skills/tech list AND from names mentioned
  inside the requirements, responsibilities, or nice-to-have text. Skip soft skills
  and generic words ("APIs", "databases") unless a specific product is named
  (e.g. "PostgreSQL", "REST", "Kubernetes").
- about_company: the "about us" / company description, if present.
- instructions: how to apply, if the posting spells out steps (email a portfolio, etc.).

JOB POSTING:
Title: {job.get('title') or ''}
Company: {job.get('company') or ''}
Location: {job.get('location') or ''}

{body[:MAX_DESCRIPTION_CHARS]}"""


_JSON_INSTRUCTION = """

Return ONLY a JSON object, nothing else — no prose, no markdown fence. Every value
is a string; use "" for anything the posting does not state:
{"work_mode": "", "seniority_level": "", "location_detail": "", "salary_text": "",
 "benefits": "", "responsibilities": "", "requirements": "", "nice_to_have": "",
 "tech_stack": "", "about_company": "", "instructions": ""}"""


def _flatten(v) -> str:
    """One field's value as clean text.

    The model is asked for a string per field and usually gives one, but it
    sometimes returns a JSON array (a list of bullet points) — especially for
    benefits, responsibilities and requirements. Left alone, str() would print a
    Python list repr like ['a', 'b'], brackets and quotes and all, which then
    lands in the DB and the UI. Join those into readable text instead.
    """
    if v is None:
        return ""
    if isinstance(v, list):
        return "; ".join(p for p in (_flatten(x) for x in v) if p)
    if isinstance(v, dict):
        return "; ".join(p for p in (_flatten(x) for x in v.values()) if p)
    return str(v).strip()


def _coerce(data: dict) -> Extraction:
    """Trust the shape, not the values: clamp the two enums, stringify the rest."""
    clean: dict = {}
    for f in FIELDS:
        clean[f] = _flatten(data.get(f, ""))

    if clean["work_mode"].lower() not in WORK_MODES:
        clean["work_mode"] = ""
    else:
        clean["work_mode"] = clean["work_mode"].lower()

    if clean["seniority_level"].lower() not in SENIORITY_LEVELS:
        clean["seniority_level"] = ""
    else:
        clean["seniority_level"] = clean["seniority_level"].lower()

    return Extraction(**clean)


def extract(job: dict) -> Extraction | None:
    """Run one extraction pass over a job. Returns None when there's nothing to read.

    None is not an error — it means the description was too thin to extract from,
    exactly as scoring returns None for the same case. The caller stores nothing
    (the fields stay NULL) and moves on.
    """
    body = strip_html(job.get("description"))
    if len(body.strip()) < MIN_DESCRIPTION_CHARS:
        return None

    # personal=False: a job description is public text, so the normal provider
    # chain is fine and no identifier leaves the machine.
    text, _provider = llm.generate(_SYSTEM, _prompt(job, body) + _JSON_INSTRUCTION,
                                   personal=False)

    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("extraction returned no JSON")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(f"extraction returned unusable JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("extraction JSON was not an object")

    try:
        return _coerce(data)
    except ValidationError as e:  # pragma: no cover — _coerce already stringifies
        raise ValueError(f"extraction failed validation: {e}") from e


def is_extracted(job: dict) -> bool:
    """Has this job already been through extraction? Used to skip on re-score."""
    return bool((job.get("extracted_at") or "").strip())
