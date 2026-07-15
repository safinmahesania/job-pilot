"""What the app has learned and what it wants you to do next.

The tracking surface, distinct from the jobs themselves: what scoring has picked up from
your save/dismiss decisions (and whether it scores through the provider chain or pins to
local Ollama), which applications are due a follow-up nudge today, and a health verdict
for every board — including the ones failing silently, which is the whole point of
looking rather than trusting a run to have said something.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.deps import _db_dep

router = APIRouter()


# ── Feedback loop ──

@router.get("/api/feedback")
def get_feedback(conn=Depends(_db_dep)):
    """What the scoring has learned from your save/dismiss decisions."""
    from src.scoring import feedback
    from src.scoring.rerank import scoring_via_chain
    data = feedback.stats(conn)
    data["scoring_via_chain"] = scoring_via_chain()
    return data


class ScoringUpdate(BaseModel):
    scoring_via_chain: bool


@router.post("/api/feedback/scoring")
def set_scoring_chain(body: ScoringUpdate, conn=Depends(_db_dep)):
    """Score through the provider chain, or pin scoring to local Ollama."""
    conn.execute("INSERT INTO settings (key,value) VALUES ('scoring_via_chain',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 ("1" if body.scoring_via_chain else "0",))
    conn.commit()
    return {"scoring_via_chain": body.scoring_via_chain}


# ── Follow-ups ──

@router.get("/api/followups")
def list_followups(conn=Depends(_db_dep)):
    """Applications that need a nudge today."""
    from src import followups
    items = followups.due(conn)
    counts = followups.summary(conn)
    return {"items": items, **counts}


class FollowupAction(BaseModel):
    action: str                # "done" | "snooze"
    days: int = 7              # for snooze


@router.post("/api/jobs/{job_id}/followup")
def set_followup(job_id: int, body: FollowupAction, conn=Depends(_db_dep)):
    from src import followups
    if body.action == "done":
        ok = followups.mark_followed_up(conn, job_id)
    elif body.action == "snooze":
        ok = followups.snooze(conn, job_id, body.days)
    else:
        raise HTTPException(400, f"unknown action: {body.action}")

    if not ok:
        raise HTTPException(404, "job not found, or it isn't an applied job")
    return {"id": job_id, "action": body.action}


# ── Source health ──

@router.get("/api/health/assess")
def assess_health(conn=Depends(_db_dep)):
    """Every board with a verdict — including the ones failing silently."""
    from src import health
    boards = health.assess(conn)
    counts = health.summary(conn)
    return {"boards": boards, **counts}