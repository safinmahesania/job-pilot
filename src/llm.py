"""LLM client with a provider fallback chain, usage tracking and UI controls.

The generator (src/apply.py) calls `generate()`. This module walks the provider
order, skipping any provider that is disabled or unconfigured, and returns the
first successful response. Every successful call records its token usage so the
AI Providers panel can show quota consumption.

Order and enabled/disabled state are stored in the settings table (editable from
the UI); defaults come from src/paths.py. Both hosted providers expose an
OpenAI-compatible chat-completions endpoint, so they share one code path over
httpx — no extra SDK dependency.
"""
import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

from src import store
from src.paths import (
    LLM_PROVIDER_ORDER, LLM_PROVIDERS,
    LLM_TEMPERATURE, LLM_TIMEOUT_SECONDS,
)

load_dotenv()

# Settings keys used to persist the UI's provider controls.
ORDER_KEY = "llm_provider_order"        # e.g. "gemini,cerebras,ollama"
DISABLED_KEY = "llm_providers_disabled" # e.g. "cerebras"


class LLMError(RuntimeError):
    """Raised when every eligible provider fails."""


# ── Usage tracking ───────────────────────────────────────────────────────────

def _utc_day() -> str:
    """Quota windows are daily; Cerebras resets at UTC midnight, so use UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def record_usage(provider: str, tokens: int):
    """Add one request (and its tokens) to today's counter for a provider."""
    conn = store.connect()
    conn.execute(
        "INSERT INTO llm_usage (day, provider, tokens, requests) VALUES (?,?,?,1) "
        "ON CONFLICT(day, provider) DO UPDATE SET "
        "tokens = tokens + excluded.tokens, requests = requests + 1",
        (_utc_day(), provider, int(tokens or 0)),
    )
    conn.commit()
    conn.close()


def usage_today() -> dict:
    """{provider: {"tokens": n, "requests": n}} for the current UTC day."""
    conn = store.connect()
    rows = conn.execute(
        "SELECT provider, tokens, requests FROM llm_usage WHERE day=?", (_utc_day(),)
    ).fetchall()
    conn.close()
    return {r[0]: {"tokens": r[1], "requests": r[2]} for r in rows}


# ── Provider controls (order + enabled), persisted in settings ───────────────

def get_order() -> list[str]:
    """The provider order, from settings if the user reordered it."""
    conn = store.connect()
    raw = store.get_setting(conn, ORDER_KEY, None)
    conn.close()
    if raw:
        saved = [p.strip() for p in raw.split(",") if p.strip() in LLM_PROVIDERS]
        # Append any provider added to the registry since the order was saved.
        return saved + [p for p in LLM_PROVIDER_ORDER if p not in saved]
    return list(LLM_PROVIDER_ORDER)


def set_order(order: list[str]):
    valid = [p for p in order if p in LLM_PROVIDERS]
    conn = store.connect()
    store.set_setting(conn, ORDER_KEY, ",".join(valid))
    conn.commit()
    conn.close()


def get_disabled() -> set[str]:
    conn = store.connect()
    raw = store.get_setting(conn, DISABLED_KEY, "")
    conn.close()
    return {p.strip() for p in (raw or "").split(",") if p.strip()}


def set_enabled(provider: str, enabled: bool):
    disabled = get_disabled()
    disabled.discard(provider) if enabled else disabled.add(provider)
    conn = store.connect()
    store.set_setting(conn, DISABLED_KEY, ",".join(sorted(disabled)))
    conn.commit()
    conn.close()


def _key_for(provider: str) -> str | None:
    env = LLM_PROVIDERS[provider].get("env")
    return os.environ.get(env) if env else None


def is_configured(provider: str) -> bool:
    """Ollama needs no key; hosted providers need theirs present in .env."""
    meta = LLM_PROVIDERS[provider]
    return True if meta.get("env") is None else bool(_key_for(provider))


def provider_status() -> list[dict]:
    """Everything the AI Providers panel renders, in the active order."""
    usage = usage_today()
    disabled = get_disabled()
    out = []
    for name in get_order():
        meta = LLM_PROVIDERS[name]
        used = usage.get(name, {"tokens": 0, "requests": 0})
        limit = meta.get("daily_tokens")
        key = _key_for(name)
        out.append({
            "name": name,
            "label": meta["label"],
            "model": meta["model"],
            "note": meta["note"],
            "env": meta.get("env"),
            "configured": is_configured(name),
            "enabled": name not in disabled,
            "key_preview": (key[:6] + "…") if key else None,
            "tokens_used": used["tokens"],
            "requests_today": used["requests"],
            "daily_tokens": limit,
            "percent": round(100 * used["tokens"] / limit, 1) if limit else None,
        })
    return out


# ── Calling ──────────────────────────────────────────────────────────────────

def _call_openai_compatible(provider: str, system: str, user: str) -> tuple[str, int]:
    """Call an OpenAI-compatible endpoint. Returns (text, tokens_used)."""
    meta = LLM_PROVIDERS[provider]
    r = httpx.post(
        f"{meta['base_url']}/chat/completions",
        headers={"Authorization": f"Bearer {_key_for(provider)}"},
        json={
            "model": meta["model"],
            "temperature": LLM_TEMPERATURE,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=LLM_TIMEOUT_SECONDS,
    )
    r.raise_for_status()
    data = r.json()
    text = data["choices"][0]["message"]["content"].strip()
    tokens = (data.get("usage") or {}).get("total_tokens", 0)
    return text, tokens


def _call_ollama(system: str, user: str) -> tuple[str, int]:
    """Local fallback. Ollama reports prompt/eval counts separately."""
    import ollama
    resp = ollama.chat(
        model=LLM_PROVIDERS["ollama"]["model"],
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        options={"temperature": LLM_TEMPERATURE},
    )
    text = resp["message"]["content"].strip()
    tokens = (resp.get("prompt_eval_count") or 0) + (resp.get("eval_count") or 0)
    return text, tokens


def generate(system: str, user: str) -> tuple[str, str]:
    """Run the prompt through the provider chain.

    Skips providers that are disabled or missing a key; moves on when a call
    fails (rate limit, timeout, network). Returns (text, provider_name) from the
    first success. Raises LLMError only if every eligible provider fails.
    """
    disabled = get_disabled()
    errors = []

    for name in get_order():
        if name in disabled:
            continue
        if not is_configured(name):
            errors.append(f"{name}: not configured")
            continue
        try:
            if name == "ollama":
                text, tokens = _call_ollama(system, user)
            else:
                text, tokens = _call_openai_compatible(name, system, user)
            if text:
                record_usage(name, tokens)
                return text, name
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue

    raise LLMError("all providers failed -> " + " | ".join(errors))
