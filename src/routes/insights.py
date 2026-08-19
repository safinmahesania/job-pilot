"""What the app has learned and what it wants you to do next.

The tracking surface, distinct from the jobs themselves: what scoring has picked
up from your save/dismiss decisions (per user), which of your applications are due
a follow-up nudge today, and a health verdict for every board.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src import store
from src.auth import current_user_id
from src.deps import _db_dep, require_admin

router = APIRouter()


# ── Feedback loop (per-user save/dismiss history) ──

@router.get("/api/feedback")
def get_feedback(user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    """What the scoring has learned from THIS user's save/dismiss decisions."""
    from src.scoring import feedback
    from src.scoring.rerank import scoring_via_chain
    data = feedback.stats(conn, user_id)
    data["scoring_via_chain"] = scoring_via_chain()
    return data


class ScoringUpdate(BaseModel):
    scoring_via_chain: bool


@router.post("/api/feedback/scoring")
def set_scoring_chain(body: ScoringUpdate,
                      _: str = Depends(require_admin), conn=Depends(_db_dep)):
    """Score through the provider chain, or pin scoring to local Ollama (global)."""
    store.set_setting(conn, "scoring_via_chain",
                      "1" if body.scoring_via_chain else "0")
    return {"scoring_via_chain": body.scoring_via_chain}


# ── Follow-ups (per-user) ──

@router.get("/api/followups")
def list_followups(user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    """Your applications that need a nudge today."""
    from src import followups
    items = followups.due(conn, user_id)
    counts = followups.summary(conn, user_id)
    return {"items": items, **counts}


class FollowupAction(BaseModel):
    action: str                # "done" | "snooze"
    days: int = 7              # for snooze


@router.post("/api/jobs/{job_id}/followup")
def set_followup(job_id: int, body: FollowupAction,
                 user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    from src import followups
    if body.action == "done":
        ok = followups.mark_followed_up(conn, user_id, job_id)
    elif body.action == "snooze":
        ok = followups.snooze(conn, user_id, job_id, body.days)
    else:
        raise HTTPException(400, f"unknown action: {body.action}")

    if not ok:
        raise HTTPException(404, "job not found, or it isn't an applied job")
    return {"id": job_id, "action": body.action}


# ── Source health (global) ──

@router.get("/api/health/assess")
def assess_health(_: str = Depends(current_user_id), conn=Depends(_db_dep)):
    """Every board with a verdict — including the ones failing silently."""
    from src import health
    boards = health.assess(conn)
    counts = health.summary(conn)
    return {"boards": boards, **counts}
