"""On-demand document generation: tailored resume and cover letter for one job.

This is the app's most careful surface. Both endpoints are rate-limited (an LLM call is
the expensive thing to abuse) and both are guarded against fabrication: the resume can
only select from your real background, and the cover letter's prose is checked so it can
never name a skill you don't have. When a guard fires, nothing is returned — a plausible
lie on an application is far more expensive than a gap, so the document is refused rather
than handed over.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from src import resume_guard, resume_fit
from src.deps import _db_dep, _get_setting, limiter
from src.paths import RATE_LIMIT_GENERATION

router = APIRouter()


def _generation_http_error(e: Exception) -> HTTPException:
    """Turn a generation failure into a message a person can act on.

    The most common failure by far is that no AI provider is configured — a new user
    with no API key and no local Ollama. The raw exception for that is
    "all providers failed -> gemini: not configured | cerebras: not configured |
    ollama: ...", which is accurate and useless to the person reading it. Catch that
    one case and say what to do about it; anything genuinely unexpected still gets the
    502 with its type, and the full traceback is already on the console.
    """
    from src.llm import LLMError

    msg = str(e)
    if isinstance(e, LLMError) or "all providers failed" in msg:
        return HTTPException(503, (
            "No AI provider is available. Add a Gemini or Cerebras API key to your "
            ".env, or run Ollama locally, then try again."))
    return HTTPException(502, f"{type(e).__name__}: {e}")


@router.post("/api/jobs/{job_id}/cover-letter")
@limiter.limit(RATE_LIMIT_GENERATION)
def cover_letter(request: Request, job_id: int, fast: bool = False,
                 conn=Depends(_db_dep)):
    """Generate a grounded cover letter for one job.

    `fast=true` skips the revise pass (one model call instead of two) — useful behind a
    proxy that times out long requests, like a Cloudflare Tunnel.
    """
    if _get_setting(conn, "generation_enabled", "1") != "1":
        raise HTTPException(
            403, "On-demand AI is off — enable it in Settings > AI features."
        )
    row = conn.execute(
        "SELECT title, company, description FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "job not found")
    try:
        from src import apply          # imported here so import errors surface
        result = apply.generate_cover_letter(dict(row), fast=fast)
    except resume_guard.FabricationError as e:
        # The letter named something the profile does not contain. It was written and
        # then refused — the same fatal stance the resume takes. Do not hand it over.
        raise HTTPException(422, {"error": "fabricated",
                                  "problems": e.problems,
                                  "message": str(e)})
    except resume_guard.ProfileIncompleteError as e:
        raise HTTPException(400, {"error": "profile_incomplete",
                                  "missing": e.missing,
                                  "message": str(e)})
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()          # full trace in the uvicorn console
        raise _generation_http_error(e)
    return result


@router.post("/api/jobs/{job_id}/resume")
@limiter.limit(RATE_LIMIT_GENERATION)
def tailored_resume(request: Request, job_id: int, conn=Depends(_db_dep)):
    """Tailor the resume template to one job."""
    if _get_setting(conn, "generation_enabled", "1") != "1":
        raise HTTPException(
            403, "On-demand AI is off — enable it in Settings > AI features."
        )
    row = conn.execute(
        "SELECT title, company, description FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "job not found")
    try:
        from src import apply
        result = apply.generate_resume(dict(row))
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))
    except resume_fit.JobDoesNotFitError as e:
        # Nothing was generated, and nothing should have been.
        raise HTTPException(422, {"error": "does_not_fit",
                                  "score": round(e.score * 100),
                                  "matched": sorted(e.matched)[:8],
                                  "message": str(e)})
    except resume_guard.ProfileIncompleteError as e:
        # Nothing was generated. Say exactly what is missing.
        raise HTTPException(400, {"error": "profile_incomplete",
                                  "missing": e.missing,
                                  "message": str(e)})
    except resume_guard.FabricationError as e:
        # Something was generated and then refused. Do not hand it over.
        raise HTTPException(422, {"error": "fabricated",
                                  "problems": e.problems,
                                  "message": str(e)})
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise _generation_http_error(e)
    return result
