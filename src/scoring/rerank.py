"""AI scoring via Ollama (local, zero cost), schema-constrained JSON."""
import json
import ollama
from pydantic import BaseModel, ValidationError

MODEL = "qwen2.5:14b"


class ScoreResult(BaseModel):
    skills_score: int        # 0-100
    seniority_score: int     # 0-100
    domain_score: int        # 0-100
    overall: int             # 0-100
    rationale: str


def _prompt(job: dict, profile: dict) -> str:
    return f"""You are scoring how well a job fits a candidate.

CANDIDATE PROFILE:
{json.dumps(profile, ensure_ascii=False, indent=2)}

JOB:
Title: {job.get('title')}
Company: {job.get('company')}
Location: {job.get('location')}
Description: {job.get('description', '')[:3000]}

Score each 0-100 (100 = perfect fit). Be strict and consistent.
- skills_score: overlap between candidate skills and job requirements
- seniority_score: how well the candidate's level matches
- domain_score: industry / role-type fit
- overall: your holistic fit score
- rationale: one concise sentence explaining the overall score.
Return only JSON matching the schema."""


def score_job(job: dict, profile: dict) -> ScoreResult | None:
    try:
        resp = ollama.chat(
            model=MODEL,
            messages=[{"role": "user", "content": _prompt(job, profile)}],
            format=ScoreResult.model_json_schema(),   # constrained decoding
            options={"temperature": 0},
        )
        return ScoreResult.model_validate_json(resp["message"]["content"])
    except (ValidationError, KeyError, Exception) as e:
        print(f"  score failed for '{job.get('title')}': {e}")
        return None