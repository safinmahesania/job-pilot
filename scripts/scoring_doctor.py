"""Why is nothing being scored? — a provider check + one live scoring attempt.

When a run shows jobs reaching scoring but 0 kept and errors > 0, the scoring step is
failing: either the hosted providers (Gemini, Cerebras) are unreachable or out of quota,
and the local Ollama fallback isn't running either. This reports each provider's status,
diagnoses the local Ollama install, then tries to score one real job.

    python -m scripts.scoring_doctor
"""
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.modules.setdefault("ollama", types.ModuleType("ollama"))

from src.env import load_env                   # noqa: E402
load_env()   # load .env into os.environ, exactly as the app does at startup —
             # without this the hosted-provider keys look missing even when they're set

from src import configio                       # noqa: E402
from src import llm                            # noqa: E402
from src.scoring import rerank                 # noqa: E402


def _diagnose_ollama():
    """Check the local ollama Python package actually exposes what we call."""
    # Drop the stub the top of this file installs, so we import the real package.
    sys.modules.pop("ollama", None)
    try:
        import ollama
    except ImportError:
        print("    ollama Python package is NOT installed.")
        print("    Fix: pip install 'ollama>=0.3'")
        return False

    ver = getattr(ollama, "__version__", "unknown")
    path = getattr(ollama, "__file__", "?")
    has_chat = hasattr(ollama, "chat")
    print(f"    package version: {ver}")
    print(f"    loaded from:     {path}")
    print(f"    has .chat():     {has_chat}")

    if not has_chat:
        print("\n    >> This is the problem. The installed ollama package is too old "
              "(or a\n       local file named 'ollama.py' is shadowing it).")
        print("       Fix: pip install --upgrade 'ollama>=0.3'")
        print("       Then check there's no ollama.py in your project root.")
        return False
    return True


def main():
    print("\n  ── Hosted providers ──")
    try:
        for p in llm.provider_status():
            state = "configured" if p["configured"] else "NO KEY"
            en = "enabled" if p["enabled"] else "disabled"
            keyp = f" key={p['key_preview']}" if p.get("key_preview") else ""
            print(f"    {p['name']:12} {p['label']:20} {state:11} {en:9}{keyp}")
    except Exception as e:
        print(f"    could not read provider status: {e}")

    print("\n  ── Local Ollama (the fallback) ──")
    ollama_ok = _diagnose_ollama()

    print(f"\n  Scoring via provider chain: {rerank.scoring_via_chain()}")

    profile = configio.read_yaml("profile.yaml") or {}
    if not profile:
        print("\n  No profile.yaml — cannot run a scoring attempt.")
        return

    job = {
        "title": "Junior Software Engineer",
        "company": "Test Co",
        "location": "Toronto, ON",
        "description": "Entry-level backend role. Python, SQL, REST APIs.",
        "job_type": "Full-time",
    }
    print("\n  ── One live scoring attempt ──")
    try:
        calibration = rerank.build_calibration()
        result = rerank.score_job(job, profile, calibration)
        if result is None:
            print("    score_job returned None — every provider failed.")
            _print_fix(ollama_ok)
        else:
            print(f"    OK — scored {result.overall} "
                  f"(skills {result.skills_score}, seniority {result.seniority_score}, "
                  f"domain {result.domain_score})")
            print("    Scoring works. If the feed is still empty, lower the score "
                  "threshold in Settings and re-run.")
    except Exception as e:
        print(f"    scoring raised: {type(e).__name__}: {e}")
        _print_fix(ollama_ok)


def _print_fix(ollama_ok):
    print("\n  ── How to fix ──")
    print("  You need ONE working scorer. Pick the easiest:")
    if not ollama_ok:
        print("   • Ollama (free, local, private): pip install --upgrade 'ollama>=0.3', ")
        print("     make sure the Ollama app/server is running, and the model is pulled.")
    else:
        print("   • Ollama package is fine — is the Ollama server running? "
              "(`ollama serve`),")
        print("     and is the model pulled? (`ollama pull <model>`)")
    print("   • Gemini (free tier): put GEMINI_API_KEY=... in .env")
    print("   • Cerebras (free tier): put CEREBRAS_API_KEY=... in .env")
    print("  Then re-run the pipeline.")


if __name__ == "__main__":
    main()
