"""A tiny LLM client with a provider fallback chain.

Used by the application-document generator (src/apply.py). The goal is
zero-cost: prefer a free hosted model for quality, but always fall back to the
local Ollama model so the feature works even with no API keys and no internet.

Order is defined by ``LLM_PROVIDER_ORDER`` in src/paths.py. For each provider we
try in turn; a provider is skipped if it isn't configured (no API key) and the
chain moves on if a call fails. The first successful response wins.

Both hosted providers (Gemini, Cerebras) expose an OpenAI-compatible
chat-completions endpoint, so they share one code path over httpx — no extra
SDK dependency. Ollama uses the package already vendored for scoring.
"""
import os

import httpx
from dotenv import load_dotenv

from src.paths import (
    LLM_PROVIDER_ORDER,
    GEMINI_MODEL, GEMINI_BASE_URL,
    CEREBRAS_MODEL, CEREBRAS_BASE_URL,
    LLM_OLLAMA_MODEL,
    LLM_TEMPERATURE, LLM_TIMEOUT_SECONDS,
)

load_dotenv()


class LLMError(RuntimeError):
    """Raised when every configured provider fails."""


def _openai_compatible(base_url: str, api_key: str, model: str,
                       system: str, user: str) -> str:
    """Call any OpenAI-compatible /chat/completions endpoint and return text."""
    r = httpx.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "temperature": LLM_TEMPERATURE,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=LLM_TIMEOUT_SECONDS,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def _try_gemini(system: str, user: str) -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise LLMError("no GEMINI_API_KEY")
    return _openai_compatible(GEMINI_BASE_URL, key, GEMINI_MODEL, system, user)


def _try_cerebras(system: str, user: str) -> str:
    key = os.environ.get("CEREBRAS_API_KEY")
    if not key:
        raise LLMError("no CEREBRAS_API_KEY")
    return _openai_compatible(CEREBRAS_BASE_URL, key, CEREBRAS_MODEL, system, user)


def _try_ollama(system: str, user: str) -> str:
    # Local fallback — always available if Ollama is running.
    import ollama
    resp = ollama.chat(
        model=LLM_OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        options={"temperature": LLM_TEMPERATURE},
    )
    return resp["message"]["content"].strip()


_PROVIDERS = {
    "gemini": _try_gemini,
    "cerebras": _try_cerebras,
    "ollama": _try_ollama,
}


def available_providers() -> list[str]:
    """Providers that are configured right now (for display in the UI)."""
    out = []
    for name in LLM_PROVIDER_ORDER:
        if name == "ollama":
            out.append(name)                       # assumed available locally
        elif name == "gemini" and os.environ.get("GEMINI_API_KEY"):
            out.append(name)
        elif name == "cerebras" and os.environ.get("CEREBRAS_API_KEY"):
            out.append(name)
    return out


def generate(system: str, user: str) -> tuple[str, str]:
    """Run the prompt through the provider chain.

    Returns ``(text, provider_name)`` from the first provider that succeeds.
    Raises ``LLMError`` only if every provider in the chain fails.
    """
    errors = []
    for name in LLM_PROVIDER_ORDER:
        fn = _PROVIDERS.get(name)
        if fn is None:
            continue
        try:
            text = fn(system, user)
            if text:
                return text, name
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue
    raise LLMError("all providers failed -> " + " | ".join(errors))
