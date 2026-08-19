"""Pinpoint why a Supabase token fails verification.

Run it with a fresh access token (copy from the browser console:
  await window.jobpilotAuth.token()
):

    python diag_auth.py "PASTE_TOKEN_HERE"

It walks the exact steps src/auth.py takes and prints the real error at whichever
one fails — instead of the generic 401 the API returns.
"""
import json
import os
import sys
import traceback
import urllib.request

from src.env import load_env

load_env()


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python diag_auth.py <token>")
    token = sys.argv[1].strip()

    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    print("SUPABASE_URL        :", repr(url))
    print("SUPABASE_ANON_KEY   :", "set" if os.environ.get("SUPABASE_ANON_KEY") else "MISSING")
    print("SUPABASE_JWT_SECRET :", "set" if os.environ.get("SUPABASE_JWT_SECRET") else "MISSING")

    import jwt
    from jwt import PyJWKClient

    header = jwt.get_unverified_header(token)
    claims = json.loads(
        __import__("base64").urlsafe_b64decode(
            token.split(".")[1] + "=" * (-len(token.split(".")[1]) % 4)))
    print("\ntoken header        :", header)
    print("token sub           :", claims.get("sub"))
    print("token aud           :", claims.get("aud"))
    print("token iss           :", claims.get("iss"))

    jwks_url = f"{url}/auth/v1/.well-known/jwks.json"
    print("\nJWKS url            :", jwks_url)

    # 1) can we fetch the JWKS at all, with a plain request (what PyJWKClient does)?
    try:
        raw = urllib.request.urlopen(jwks_url, timeout=10).read()
        data = json.loads(raw)
        print("JWKS fetch (plain)  : OK —",
              [(k.get("kid"), k.get("alg")) for k in data.get("keys", [])])
    except Exception as e:
        print("JWKS fetch (plain)  : FAILED —", type(e).__name__, e)
        # retry with the apikey header, in case the endpoint requires it
        try:
            req = urllib.request.Request(
                jwks_url, headers={"apikey": os.environ.get("SUPABASE_ANON_KEY", "")})
            data = json.loads(urllib.request.urlopen(req, timeout=10).read())
            print("JWKS fetch (apikey) : OK —",
                  [(k.get("kid"), k.get("alg")) for k in data.get("keys", [])])
            print(">>> The JWKS endpoint needs the apikey header. That's the fix.")
        except Exception as e2:
            print("JWKS fetch (apikey) : FAILED —", type(e2).__name__, e2)

    # 2) PyJWKClient signing-key lookup by kid
    try:
        client = PyJWKClient(jwks_url)
        key = client.get_signing_key_from_jwt(token)
        print("\nsigning key match   : OK (kid found in JWKS)")
    except Exception:
        print("\nsigning key match   : FAILED")
        traceback.print_exc()
        return

    # 3) full decode
    try:
        out = jwt.decode(token, key.key, algorithms=["ES256", "RS256"],
                         options={"verify_aud": False})
        print("decode              : OK — sub =", out.get("sub"))
        print("\n>>> Verification SUCCEEDS here. If the API still 401s, the running "
              "server has stale env or old code — restart uvicorn.")
    except Exception:
        print("decode              : FAILED")
        traceback.print_exc()


if __name__ == "__main__":
    main()
