"""AI scoring via Ollama (local, zero cost), schema-constrained JSON.

Model selection:
  - PRIMARY (14b) is the default, best quality.
  - If a job fails on the primary (OOM/CUDA/etc.), that job falls back to
    FALLBACK (7b) — but only for that run. reset_model_state() (called at the
    start of every run) puts it back to primary.
  - A user-picked model from Settings overrides PRIMARY via set_preferred().
"""
import ollama
from pydantic import BaseModel

from src.paths import (
    MODEL_PRIMARY as PRIMARY,
    MODEL_FALLBACK as FALLBACK,
    MODEL_NUM_CTX,
    MODEL_TEMPERATURE,
    SCORE_WEIGHT_SKILLS,
    SCORE_WEIGHT_SENIORITY,
    SCORE_WEIGHT_DOMAIN,
)

# live scoring state — the UI reads this to show which model is active
MODEL_STATE = {"active": PRIMARY, "fallback_active": False, "preferred": PRIMARY}


def set_preferred(model: str):
    """User picked a model in Settings. Becomes the run's starting model."""
    if model in (PRIMARY, FALLBACK):
        MODEL_STATE["preferred"] = model
        MODEL_STATE["active"] = model


def reset_model_state():
    """Call at the START of every run — back to the preferred model."""
    MODEL_STATE["active"] = MODEL_STATE["preferred"]
    MODEL_STATE["fallback_active"] = False


def get_model_state() -> dict:
    return dict(MODEL_STATE)


SCORING_GUIDE = """
Score EACH dimension across the full 0-100 range — do not cluster around 60-75.

What a high score looks like on each:
  skills_score 90+   : job's required tech overlaps the candidate's core skills
  seniority_score 90+: intern / junior / new-grad role, no senior requirement
  domain_score 90+   : IT/software work matching the candidate's experience

  40-59 on any dimension = weak (adjacent domain, level slightly above candidate)
  0-39 on any dimension  = poor (wrong domain, wrong country, clearly senior)

A junior software role in Toronto matching the candidate's stack SHOULD score 85+
on skills and seniority. Do not be conservative.
"""


class ScoreResult(BaseModel):
    skills_score: int        # 0-100
    seniority_score: int     # 0-100
    domain_score: int        # 0-100
    overall: int             # 0-100 (recomputed as weighted composite below)
    rationale: str


def _candidate_summary(profile: dict) -> str:
    parts = []
    if profile.get("summary"):
        parts.append("SUMMARY:\n" + profile["summary"].strip())
    parts.append("SENIORITY: " + str(profile.get("seniority", "n/a")))

    skills = profile.get("skills", {})
    tiers = [f"{t}: {', '.join(skills[t])}" for t in ("expert", "proficient", "familiar") if skills.get(t)]
    if tiers:
        parts.append("SKILLS:\n" + "\n".join(tiers))

    if profile.get("experience"):
        lines = []
        for e in profile["experience"]:
            lines.append(f"- {e.get('role')} @ {e.get('company')} ({e.get('start','?')}-{e.get('end','?')})")
            lines += [f"    - {h}" for h in e.get("highlights", [])]
        parts.append("EXPERIENCE:\n" + "\n".join(lines))

    if profile.get("projects"):
        lines = []
        for p in profile["projects"]:
            lines.append(f"- {p.get('name')} [{', '.join(p.get('tech', []))}]: {p.get('description','')}")
            lines += [f"    - {h}" for h in p.get("highlights", [])]
        parts.append("PROJECTS:\n" + "\n".join(lines))

    if profile.get("education"):
        edu = []
        for e in profile["education"]:
            edu.append(f"- {e.get('degree')} in {e.get('field')}, {e.get('institution')} ({e.get('end','?')})")
        parts.append("EDUCATION:\n" + "\n".join(edu))

    return "\n\n".join(parts)


def _prompt(job: dict, profile: dict) -> str:
    return f"""{SCORING_GUIDE}

CANDIDATE:
{_candidate_summary(profile)}

JOB:
Title: {job.get('title')}
Company: {job.get('company')}
Location: {job.get('location')}
Type: {job.get('job_type') or 'n/a'}
Description: {(job.get('description') or '')[:3500]}

Score each dimension 0-100. Weigh the candidate's real experience and projects
against the job's requirements.
- skills_score: overlap of candidate skills/tech with job requirements
- seniority_score: fit between candidate level and role level
- domain_score: relevance of candidate's experience/projects to the role
- overall: holistic fit
- rationale: one concise sentence citing specific skills/experience.
Return only JSON matching the schema."""


def _call(model: str, job: dict, profile: dict) -> ScoreResult:
    resp = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": _prompt(job, profile)}],
        format=ScoreResult.model_json_schema(),   # constrained decoding
        options={"temperature": MODEL_TEMPERATURE, "num_ctx": MODEL_NUM_CTX},
    )
    return ScoreResult.model_validate_json(resp["message"]["content"])


def score_job(job: dict, profile: dict) -> ScoreResult | None:
    try:
        result = _call(MODEL_STATE["active"], job, profile)
    except Exception as e:
        # primary failed — try the fallback model for THIS job (run-scoped)
        if MODEL_STATE["active"] != FALLBACK:
            print(f"  {MODEL_STATE['active']} failed ({str(e)[:50]}) - falling back to {FALLBACK}")
            MODEL_STATE["active"] = FALLBACK
            MODEL_STATE["fallback_active"] = True
            try:
                result = _call(FALLBACK, job, profile)
            except Exception as e2:
                print(f"  fallback also failed for '{job.get('title')}': {e2}")
                return None
        else:
            print(f"  score failed for '{job.get('title')}': {e}")
            return None

    # model's own `overall` is unreliable (clusters low) — recompute
    result.overall = round(SCORE_WEIGHT_SKILLS * result.skills_score
                           + SCORE_WEIGHT_SENIORITY * result.seniority_score
                           + SCORE_WEIGHT_DOMAIN * result.domain_score)
    return result
