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

# Environment is read at request time, not import time: src.api imports this module
# (via src.deps) BEFORE it calls load_env(), so anything bound at import would freeze
# the empty pre-.env values and every asymmetric verify would fail with a missing URL.
_jwks_client: PyJWKClient | None = None
_jwks_client_url: str | None = None


def _supabase_url() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def _jwt_secret() -> str | None:
    return os.environ.get("SUPABASE_JWT_SECRET")


def _aud() -> str | None:
    """Expected audience. Supabase signs user access tokens with aud="authenticated",
    so verifying it rejects tokens minted for a different audience. Override with
    SUPABASE_JWT_AUD for a non-standard project, or set it to "" to disable the check
    entirely (an escape hatch if a project's tokens ever carry a different aud)."""
    return os.environ.get("SUPABASE_JWT_AUD", "authenticated") or None


def _jwks() -> PyJWKClient:
    """Lazily build (and cache) the JWKS client for the current SUPABASE_URL. Keys
    are cached in memory, so the Auth server isn't in the hot path of every request."""
    global _jwks_client, _jwks_client_url
    url = _supabase_url()
    if not url:
        raise RuntimeError("SUPABASE_URL is not set — cannot verify asymmetric JWTs.")
    jwks_url = f"{url}/auth/v1/.well-known/jwks.json"
    if _jwks_client is None or _jwks_client_url != jwks_url:
        _jwks_client = PyJWKClient(jwks_url)
        _jwks_client_url = jwks_url
    return _jwks_client


def verify_token(token: str) -> dict:
    """Verify a Supabase access token's signature and expiry; return its claims.

    Raises jwt.PyJWTError (or RuntimeError if the needed config is missing) when
    the token can't be trusted.
    """
    alg = jwt.get_unverified_header(token).get("alg", "")
    if alg == "HS256":
        secret = _jwt_secret()
        if not secret:
            raise RuntimeError("SUPABASE_JWT_SECRET is not set — cannot verify HS256 JWTs.")
        aud = _aud()
        return jwt.decode(token, secret, algorithms=["HS256"],
                          audience=aud, options={} if aud else {"verify_aud": False})
    # Asymmetric: fetch the matching public key from the project's JWKS.
    signing_key = _jwks().get_signing_key_from_jwt(token)
    aud = _aud()
    return jwt.decode(token, signing_key.key, algorithms=["ES256", "RS256"],
                      audience=aud, options={} if aud else {"verify_aud": False})


def user_id_from_token(token: str) -> str:
    sub = verify_token(token).get("sub")
    if not sub:
        raise ValueError("token has no sub claim")
    return sub


def _user_id_from_ext_key(key: str):
    """Resolve a per-user extension key to its user id, or None if it doesn't match."""
    key = (key or "").strip()
    if not key:
        return None
    from src import db
    conn = db.connect()
    try:
        row = conn.execute("SELECT id FROM users WHERE ext_key = ?", (key,)).fetchone()
        return str(row[0]) if row else None
    finally:
        conn.close()


def current_user_id(authorization: str = Header(None),
                    x_jobpilot_key: str = Header(None)) -> str:
    """FastAPI dependency: return the caller's uuid, authenticated one of two ways.

    - Browser extension: a per-user ``X-JobPilot-Key`` header (the key shown in the
      web app). Checked first so the extension needs no Supabase session.
    - Web app: a Supabase ``Authorization: Bearer <jwt>`` header.

    Routes declare ``user_id: str = Depends(current_user_id)``. Anything unverifiable
    is a 401.
    """
    if x_jobpilot_key:
        uid = _user_id_from_ext_key(x_jobpilot_key)
        if uid:
            return uid
        raise HTTPException(401, "Invalid extension key")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        return user_id_from_token(token)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Invalid or expired token") from None
