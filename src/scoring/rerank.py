"""AI scoring via Ollama (local, zero cost), schema-constrained JSON."""
import ollama
from pydantic import BaseModel

MODEL = "qwen2.5:14b"   # OOM aaye to "qwen2.5:7b" kar de

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
            lines.append(f"- {e.get('role')} @ {e.get('company')} ({e.get('start','?')}–{e.get('end','?')})")
            lines += [f"    • {h}" for h in e.get("highlights", [])]
        parts.append("EXPERIENCE:\n" + "\n".join(lines))

    if profile.get("projects"):
        lines = []
        for p in profile["projects"]:
            lines.append(f"- {p.get('name')} [{', '.join(p.get('tech', []))}]: {p.get('description','')}")
            lines += [f"    • {h}" for h in p.get("highlights", [])]
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


def score_job(job: dict, profile: dict) -> ScoreResult | None:
    try:
        resp = ollama.chat(
            model=MODEL,
            messages=[{"role": "user", "content": _prompt(job, profile)}],
            format=ScoreResult.model_json_schema(),   # constrained decoding
            options={"temperature": 0, "num_ctx": 4096},
        )
        result = ScoreResult.model_validate_json(resp["message"]["content"])

        # model's own `overall` is unreliable (clusters low) — recompute
        result.overall = round(0.5 * result.skills_score
                               + 0.3 * result.seniority_score
                               + 0.2 * result.domain_score)
        return result
    except Exception as e:
        print(f"  score failed for '{job.get('title')}': {e}")
        return None