"""User-facing settings: score threshold, schedule, AI feature toggles, privacy mode.

All of these persist to the settings table (except the schedule's live state, which the
scheduler owns). The values are clamped or whitelisted on the way in — a threshold to
0-100, an interval to a sane range of hours, a privacy mode to the three it can be — so a
bad value from a form never reaches the rest of the app.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src import scheduler
from src.deps import _db_dep, _get_setting

router = APIRouter()


# ── Score threshold ──

@router.get("/api/settings")
def get_settings(conn=Depends(_db_dep)):
    t = int(_get_setting(conn, "score_threshold", 70))
    return {"score_threshold": t}


class ThresholdUpdate(BaseModel):
    value: int


@router.post("/api/settings/threshold")
def set_threshold(body: ThresholdUpdate, conn=Depends(_db_dep)):
    v = max(0, min(100, body.value))
    conn.execute("INSERT INTO settings (key,value) VALUES ('score_threshold',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(v),))
    conn.commit()
    return {"score_threshold": v}


# ── Fetch schedule ──

@router.get("/api/schedule")
def get_schedule(conn=Depends(_db_dep)):
    enabled = _get_setting(conn, "scheduler_enabled", "1") == "1"
    hours = float(_get_setting(conn, "run_interval_hours", "8") or 8)
    s = scheduler.get_state()
    return {"enabled": enabled, "interval_hours": hours,
            "last_run": s["last_run"], "next_run": s["next_run"], "running": s["running"]}


class ScheduleUpdate(BaseModel):
    enabled: bool
    interval_hours: float


@router.post("/api/schedule")
def set_schedule(body: ScheduleUpdate, conn=Depends(_db_dep)):
    hours = max(0.5, min(168.0, body.interval_hours))
    for k, v in (("scheduler_enabled", "1" if body.enabled else "0"),
                 ("run_interval_hours", str(hours))):
        conn.execute("INSERT INTO settings (key,value) VALUES (?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    conn.commit()
    return {"enabled": body.enabled, "interval_hours": hours}


# ── AI feature toggles ──

@router.get("/api/ai-features")
def get_ai_features(conn=Depends(_db_dep)):
    scoring = _get_setting(conn, "scoring_enabled", "1") == "1"
    generation = _get_setting(conn, "generation_enabled", "1") == "1"
    return {"scoring": scoring, "generation": generation}


class AIFeature(BaseModel):
    feature: str          # "scoring" | "generation"
    enabled: bool


@router.post("/api/ai-features")
def set_ai_features(body: AIFeature, conn=Depends(_db_dep)):
    keys = {"scoring": "scoring_enabled", "generation": "generation_enabled"}
    if body.feature not in keys:
        raise HTTPException(400, "unknown feature")
    conn.execute("INSERT INTO settings (key,value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (keys[body.feature], "1" if body.enabled else "0"))
    conn.commit()
    return {"feature": body.feature, "enabled": body.enabled}


# ── Privacy mode ──

@router.get("/api/privacy")
def get_privacy():
    from src import llm, importers
    from src.paths import PRIVACY_MODE
    return {"mode": llm.privacy_mode(),
            "default": PRIVACY_MODE,
            "follow_job_links": importers.follow_links_enabled()}


class PrivacyUpdate(BaseModel):
    mode: str | None = None                 # "redacted" | "local" | "full"
    follow_job_links: bool | None = None


@router.post("/api/privacy")
def set_privacy(body: PrivacyUpdate, conn=Depends(_db_dep)):
    if body.mode is not None:
        if body.mode not in ("redacted", "local", "full"):
            raise HTTPException(400, f"unknown privacy mode: {body.mode}")
        conn.execute("INSERT INTO settings (key,value) VALUES ('privacy_mode',?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (body.mode,))
    if body.follow_job_links is not None:
        conn.execute("INSERT INTO settings (key,value) VALUES ('follow_job_links',?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     ("1" if body.follow_job_links else "0",))
    conn.commit()

    from src import llm, importers
    return {"mode": llm.privacy_mode(),
            "follow_job_links": importers.follow_links_enabled()}
