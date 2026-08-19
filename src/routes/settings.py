"""Settings, split by scope.

  * score_threshold is PER-USER (user_settings) — each person's feed cutoff.
  * the fetch schedule and AI feature toggles are GLOBAL/admin (app_settings).
  * privacy mode stays global for now (llm.py reads it globally); it can move
    per-user when the LLM layer is made user-aware.

Values are clamped/whitelisted on the way in so a bad form value never reaches
the rest of the app.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src import scheduler, store
from src.auth import current_user_id
from src.deps import (_db_dep, _get_setting, _set_user_setting,
                      _user_threshold, require_admin)

router = APIRouter()


# ── Score threshold (per-user) ──

@router.get("/api/settings")
def get_settings(user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    return {"score_threshold": _user_threshold(conn, user_id)}


class ThresholdUpdate(BaseModel):
    value: int


@router.post("/api/settings/threshold")
def set_threshold(body: ThresholdUpdate,
                  user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    v = max(0, min(100, body.value))
    _set_user_setting(conn, user_id, "score_threshold", v)
    return {"score_threshold": v}


# ── Fetch schedule (global/admin) ──

@router.get("/api/schedule")
def get_schedule(conn=Depends(_db_dep), _=Depends(current_user_id)):
    enabled = _get_setting(conn, "scheduler_enabled", "1") == "1"
    hours = float(_get_setting(conn, "run_interval_hours", "8") or 8)
    s = scheduler.get_state()
    return {"enabled": enabled, "interval_hours": hours,
            "last_run": s["last_run"], "next_run": s["next_run"],
            "running": s["running"]}


class ScheduleUpdate(BaseModel):
    enabled: bool
    interval_hours: float


@router.post("/api/schedule")
def set_schedule(body: ScheduleUpdate,
                 _: str = Depends(require_admin), conn=Depends(_db_dep)):
    hours = max(0.5, min(168.0, body.interval_hours))
    store.set_setting(conn, "scheduler_enabled", "1" if body.enabled else "0")
    store.set_setting(conn, "run_interval_hours", str(hours))
    return {"enabled": body.enabled, "interval_hours": hours}


# ── AI feature toggles (global/admin) ──

@router.get("/api/ai-features")
def get_ai_features(conn=Depends(_db_dep), _=Depends(current_user_id)):
    scoring = _get_setting(conn, "scoring_enabled", "1") == "1"
    generation = _get_setting(conn, "generation_enabled", "1") == "1"
    return {"scoring": scoring, "generation": generation}


class AIFeature(BaseModel):
    feature: str          # "scoring" | "generation"
    enabled: bool


@router.post("/api/ai-features")
def set_ai_features(body: AIFeature,
                    _: str = Depends(require_admin), conn=Depends(_db_dep)):
    keys = {"scoring": "scoring_enabled", "generation": "generation_enabled"}
    if body.feature not in keys:
        raise HTTPException(400, "unknown feature")
    store.set_setting(conn, keys[body.feature], "1" if body.enabled else "0")
    return {"feature": body.feature, "enabled": body.enabled}


# ── Privacy mode (global for now) ──

@router.get("/api/privacy")
def get_privacy(_=Depends(current_user_id)):
    from src import llm, importers
    from src.paths import PRIVACY_MODE
    return {"mode": llm.privacy_mode(),
            "default": PRIVACY_MODE,
            "follow_job_links": importers.follow_links_enabled()}


class PrivacyUpdate(BaseModel):
    mode: str | None = None                 # "redacted" | "local" | "full"
    follow_job_links: bool | None = None


@router.post("/api/privacy")
def set_privacy(body: PrivacyUpdate,
                _: str = Depends(current_user_id), conn=Depends(_db_dep)):
    if body.mode is not None:
        if body.mode not in ("redacted", "local", "full"):
            raise HTTPException(400, f"unknown privacy mode: {body.mode}")
        store.set_setting(conn, "privacy_mode", body.mode)
    if body.follow_job_links is not None:
        store.set_setting(conn, "follow_job_links",
                          "1" if body.follow_job_links else "0")

    from src import llm, importers
    return {"mode": llm.privacy_mode(),
            "follow_job_links": importers.follow_links_enabled()}
