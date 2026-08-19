"""Verify Supabase-issued JWTs and resolve the user id from a request.

The frontend logs in through Supabase Auth and sends its access token as
``Authorization: Bearer <jwt>``. This verifies that token's signature and returns
the user id — the ``sub`` claim, which is auth.users.id (and public.users.id).

Supabase signs tokens one of two ways and a project may use either, so the path
is chosen from the token header's ``alg`` and both work with no config flag:
  * asymmetric ES256/RS256 — verified with the project's public key from the JWKS
    endpoint, ``https://<ref>.supabase.co/auth/v1/.well-known/jwks.json``;
  * legacy HS256 — verified with the project's JWT secret (SUPABASE_JWT_SECRET).
"""
import os

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET")
_JWKS_URL = (f"{_SUPABASE_URL}/auth/v1/.well-known/jwks.json"
             if _SUPABASE_URL else None)

_jwks_client: PyJWKClient | None = None


def _jwks() -> PyJWKClient:
    """Lazily build the JWKS client. It caches keys in memory, so the Auth server
    isn't in the hot path of every request."""
    global _jwks_client
    if _jwks_client is None:
        if not _JWKS_URL:
            raise RuntimeError("SUPABASE_URL is not set — cannot verify asymmetric JWTs.")
        _jwks_client = PyJWKClient(_JWKS_URL)
    return _jwks_client


def verify_token(token: str) -> dict:
    """Verify a Supabase access token's signature and expiry; return its claims.

    Raises jwt.PyJWTError (or RuntimeError if the needed config is missing) when
    the token can't be trusted.
    """
    alg = jwt.get_unverified_header(token).get("alg", "")
    if alg == "HS256":
        if not _JWT_SECRET:
            raise RuntimeError("SUPABASE_JWT_SECRET is not set — cannot verify HS256 JWTs.")
        return jwt.decode(token, _JWT_SECRET, algorithms=["HS256"],
                          options={"verify_aud": False})
    # Asymmetric: fetch the matching public key from the project's JWKS.
    signing_key = _jwks().get_signing_key_from_jwt(token)
    return jwt.decode(token, signing_key.key, algorithms=["ES256", "RS256"],
                      options={"verify_aud": False})


def user_id_from_token(token: str) -> str:
    sub = verify_token(token).get("sub")
    if not sub:
        raise ValueError("token has no sub claim")
    return sub


def current_user_id(authorization: str = Header(None)) -> str:
    """FastAPI dependency: verify the Bearer token, return the user's uuid (sub).

    Routes declare ``user_id: str = Depends(current_user_id)`` to get the caller's
    id, already authenticated. Anything unverifiable is a 401.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        return user_id_from_token(token)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Invalid or expired token") from None
