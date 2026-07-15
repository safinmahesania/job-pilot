"""AI providers, the scoring model, notifications, and connection tests.

Everything about the machinery behind generation and scoring: which local model scores
jobs, which generation providers are configured and in what order the fallback chain
tries them, whether Telegram notifications are on, and the two "does this actually work"
buttons that send a tiny prompt or a test message through the real path.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.deps import _db_dep, _get_setting

router = APIRouter()


# ── Scoring model ──

@router.get("/api/model")
def model_state():
    from src.scoring.rerank import get_model_state
    return get_model_state()


class ModelUpdate(BaseModel):
    model: str


@router.post("/api/model")
def set_model(body: ModelUpdate, conn=Depends(_db_dep)):
    from src.scoring.rerank import set_preferred, get_model_state
    set_preferred(body.model)
    conn.execute("INSERT INTO settings (key,value) VALUES ('scoring_model',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (body.model,))
    conn.commit()
    return get_model_state()


# ── Notifications ──

@router.get("/api/notify")
def get_notify(conn=Depends(_db_dep)):
    from src import notify
    enabled = _get_setting(conn, "notify_enabled", "1") == "1"
    return {"enabled": enabled, "configured": bool(notify._token() and notify._chat_id())}


class NotifyUpdate(BaseModel):
    enabled: bool


@router.post("/api/notify")
def set_notify(body: NotifyUpdate, conn=Depends(_db_dep)):
    conn.execute("INSERT INTO settings (key,value) VALUES ('notify_enabled',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 ("1" if body.enabled else "0",))
    conn.commit()
    return {"enabled": body.enabled}


@router.post("/api/notify/test")
def test_notify():
    from src import notify
    ok = notify.send("JobPilot test — notifications working ✅")
    return {"sent": ok}


@router.post("/api/notify/test-digest")
def send_test_digest(conn=Depends(_db_dep)):
    """Send this week's digest now, so you can see what it looks like."""
    from src import health, notify
    stats = health.week_stats(conn)

    message = notify.weekly_digest(stats)
    if not notify.enabled():
        return {"sent": False, "preview": message,
                "reason": "Telegram isn't configured, or notifications are off."}
    return {"sent": notify.send(message), "preview": message}


# ── Connection tests ──

@router.post("/api/llm/test")
def llm_test():
    """Send a tiny prompt through the provider chain to verify it works."""
    from src import llm
    try:
        text, provider = llm.generate(
            "You are a connection test. Reply with exactly: OK",
            "Reply with exactly: OK",
        )
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"ok": True, "provider": provider, "reply": text[:80]}


# ── Configuration files ──

@router.get("/api/config/files")
def config_files():
    """Paths of the files the user edits, plus whether each one exists."""
    from src.paths import CONFIG_FILES, ROOT
    out = []
    for f in CONFIG_FILES:
        out.append({**f, "exists": (ROOT / f["path"]).exists()})
    return {"files": out, "root": str(ROOT)}


# ── AI providers (status, enable/disable, reorder) ──

@router.get("/api/llm/providers")
def llm_providers():
    """Status of every generation provider: config, quota usage, enabled."""
    from src import llm
    providers = llm.provider_status()
    tracked = [p for p in providers if p["daily_tokens"]]
    return {
        "providers": providers,
        "available": sum(1 for p in providers if p["configured"] and p["enabled"]),
        "total": len(providers),
        "combined_tokens": sum(p["tokens_used"] for p in tracked),
        "combined_limit": sum(p["daily_tokens"] for p in tracked),
    }


class ProviderToggle(BaseModel):
    enabled: bool


@router.post("/api/llm/providers/{name}/toggle")
def llm_provider_toggle(name: str, body: ProviderToggle):
    from src import llm
    from src.paths import LLM_PROVIDERS
    if name not in LLM_PROVIDERS:
        raise HTTPException(404, "unknown provider")
    llm.set_enabled(name, body.enabled)
    return {"name": name, "enabled": body.enabled}


class ProviderOrder(BaseModel):
    order: list[str]


@router.post("/api/llm/providers/order")
def llm_provider_order(body: ProviderOrder):
    """Reorder the fallback chain (first = tried first)."""
    from src import llm
    llm.set_order(body.order)
    return {"order": llm.get_order()}