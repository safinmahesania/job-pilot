"""AI scoring: how well does this job fit this candidate?

Scoring now runs through the same provider chain as everything else. It used to be
pinned to local Ollama on the theory that per-run volume would burn a free quota —
but once the database is warm a run scores a handful of genuinely new jobs, not
hundreds, and Cerebras alone allows a million tokens a day. Moving scoring onto the
chain makes a run dramatically faster and the judgement noticeably better. Ollama
stays at the end of the chain, so an offline machine still works.

Two things keep the output trustworthy rather than merely plausible:

  * On Ollama we use constrained decoding — the model is forced to emit JSON
    matching the schema, so it cannot ramble. Hosted providers have no equivalent,
    so their JSON is asked for explicitly and parsed defensively. A response that
    won't parse is a failure, not an excuse to invent a number.

  * The model's own `overall` is discarded. It clusters around 65-75 whatever the
    input. The real overall is recomputed here as a weighted composite of the
    three dimensions, which actually separates a good match from a mediocre one.
"""
import json
import re

from pydantic import BaseModel, ValidationError

from src import llm, store
from src.scoring import feedback
from src.paths import (
    MODEL_PRIMARY as PRIMARY,
    MODEL_FALLBACK as FALLBACK,
    MODEL_NUM_CTX,
    MODEL_TEMPERATURE,
    SCORE_WEIGHT_SKILLS,
    SCORE_WEIGHT_SENIORITY,
    SCORE_WEIGHT_DOMAIN,
    SCORING_VIA_PROVIDER_CHAIN,
)

# Live state, read by the UI to show what actually scored the last job.
MODEL_STATE = {"active": PRIMARY, "fallback_active": False,
               "preferred": PRIMARY, "provider": "ollama"}


def set_preferred(model: str):
    """The Ollama model to use when Ollama is the provider."""
    if model in (PRIMARY, FALLBACK):
        MODEL_STATE["preferred"] = model
        MODEL_STATE["active"] = model


def reset_model_state():
    """Called at the start of every run."""
    MODEL_STATE["active"] = MODEL_STATE["preferred"]
    MODEL_STATE["fallback_active"] = False


def get_model_state() -> dict:
    return dict(MODEL_STATE)


def scoring_via_chain() -> bool:
    """Settings override the default."""
    try:
        conn = store.connect()
        val = store.get_setting(conn, "scoring_via_chain", None)
        conn.close()
        if val is not None:
            return val == "1"
    except Exception:
        pass
    return SCORING_VIA_PROVIDER_CHAIN


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
    overall: int             # 0-100 — recomputed below; the model's is unreliable
    rationale: str


def _candidate_summary(profile: dict) -> str:
    """The candidate's background.

    Carries no name, email, phone or address — scoring never needed them, so it
    never sees them, in any privacy mode.
    """
    parts = []
    if profile.get("summary"):
        parts.append("SUMMARY:\n" + profile["summary"].strip())
    parts.append("SENIORITY: " + str(
        (profile.get("identity", {}) or {}).get("seniority")
        or profile.get("seniority", "n/a")
    ))

    skills = profile.get("skills", {}) or {}
    tiers = [f"{t}: {', '.join(skills[t])}"
             for t in ("expert", "proficient", "familiar") if skills.get(t)]
    if tiers:
        parts.append("SKILLS:\n" + "\n".join(tiers))

    if profile.get("experience"):
        lines = []
        for e in profile["experience"]:
            lines.append(f"- {e.get('role')} @ {e.get('company')} "
                         f"({e.get('start','?')}-{e.get('end','?')})")
            lines += [f"    - {h}" for h in e.get("highlights", [])]
        parts.append("EXPERIENCE:\n" + "\n".join(lines))

    if profile.get("projects"):
        lines = []
        for p in profile["projects"]:
            lines.append(f"- {p.get('name')} [{', '.join(p.get('tech', []))}]: "
                         f"{p.get('description','')}")
            lines += [f"    - {h}" for h in p.get("highlights", [])]
        parts.append("PROJECTS:\n" + "\n".join(lines))

    if profile.get("education"):
        edu = [f"- {e.get('degree')} in {e.get('field')}, {e.get('institution')} "
               f"({e.get('end','?')})" for e in profile["education"]]
        parts.append("EDUCATION:\n" + "\n".join(edu))

    return "\n\n".join(parts)


def _prompt(job: dict, profile: dict, calibration: str = "") -> str:
    block = f"\n{calibration}\n" if calibration else ""
    return f"""{SCORING_GUIDE}

CANDIDATE:
{_candidate_summary(profile)}
{block}
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
- rationale: one concise sentence citing specific skills/experience."""


_JSON_INSTRUCTION = """

Return ONLY a JSON object, nothing else — no prose, no markdown fence:
{"skills_score": 0-100, "seniority_score": 0-100, "domain_score": 0-100,
 "overall": 0-100, "rationale": "one sentence"}"""


# ── Providers ───────────────────────────────────────────────────────────────

def _call_ollama(model: str, prompt: str) -> ScoreResult:
    """Local model with constrained decoding — it cannot emit invalid JSON."""
    import ollama
    resp = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        format=ScoreResult.model_json_schema(),
        options={"temperature": MODEL_TEMPERATURE, "num_ctx": MODEL_NUM_CTX},
    )
    return ScoreResult.model_validate_json(resp["message"]["content"])


def _call_chain(prompt: str) -> tuple[ScoreResult, str]:
    """The provider chain. No constrained decoding, so parse defensively."""
    system = ("You score how well a job fits a candidate. You are precise, "
              "decisive, and you use the full 0-100 range.")

    # personal=True: the prompt carries the candidate's background. In local mode
    # it never leaves the machine; in redacted mode it carries no direct
    # identifier (see _candidate_summary) — scoring never needed one.
    text, provider = llm.generate(system, prompt + _JSON_INSTRUCTION, personal=True)

    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"{provider} returned no JSON")
    try:
        return ScoreResult.model_validate(json.loads(match.group(0))), provider
    except (json.JSONDecodeError, ValidationError) as e:
        raise ValueError(f"{provider} returned unusable JSON: {e}") from e


# ── Entry point ─────────────────────────────────────────────────────────────

def score_job(job: dict, profile: dict, calibration: str = "") -> ScoreResult | None:
    """Score one job, or return None. Never a made-up number.

    `calibration` is the feedback block from src.scoring.feedback. The caller
    builds it once per run (it needs a database read) and passes it in.
    """
    prompt = _prompt(job, profile, calibration)
    result = None

    if scoring_via_chain():
        try:
            result, provider = _call_chain(prompt)
            MODEL_STATE["provider"] = provider
        except Exception as e:
            print(f"  chain scoring failed for '{job.get('title')}': "
                  f"{str(e)[:80]} — falling back to local Ollama")

    if result is None:
        # The chain is off, or every provider in it failed. Ollama direct, with
        # constrained decoding: the most reliable path available.
        try:
            result = _call_ollama(MODEL_STATE["active"], prompt)
            MODEL_STATE["provider"] = "ollama"
        except Exception as e:
            if MODEL_STATE["active"] != FALLBACK:
                print(f"  {MODEL_STATE['active']} failed ({str(e)[:50]}) — "
                      f"trying {FALLBACK}")
                MODEL_STATE["active"] = FALLBACK
                MODEL_STATE["fallback_active"] = True
                try:
                    result = _call_ollama(FALLBACK, prompt)
                    MODEL_STATE["provider"] = "ollama"
                except Exception as e2:
                    print(f"  scoring failed for '{job.get('title')}': {e2}")
                    return None
            else:
                print(f"  scoring failed for '{job.get('title')}': {e}")
                return None

    result.overall = round(SCORE_WEIGHT_SKILLS * result.skills_score
                           + SCORE_WEIGHT_SENIORITY * result.seniority_score
                           + SCORE_WEIGHT_DOMAIN * result.domain_score)
    return result


def build_calibration() -> str:
    """The feedback block for this run — built once, reused for every job."""
    try:
        conn = store.connect()
        block = feedback.examples(conn)
        conn.close()
        return block
    except Exception:
        return ""
