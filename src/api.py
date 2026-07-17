"""FastAPI backend for JobPilot — serves jobs, status updates, and the frontend."""
from pathlib import Path
import os
import secrets
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from src import scheduler
from src.deps import (_db_dep)
from src.logs import log
from src import __version__
from src.env import load_env
load_env()
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from src.deps import limiter
app = FastAPI(title="JobPilot", version=__version__)

# Rate limiting. The limiter itself lives in deps.py so the route modules can share it;
# here we wire it to the app and its 429 handler. A single real user never approaches
# the limits — they exist so a public tunnel URL cannot be turned into free LLM compute
# or a parse-DoS by a bot.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# The browser extension runs on ATS pages and calls this API from a
# chrome-extension:// origin, so those requests must be allowed through.
# CORS for the browser extension only.
#
# The extension runs on ATS pages and calls this API from a chrome-extension:// (or
# moz-extension://) origin, so those origins are allowed. Two deliberate narrowings
# from the audit:
#
#   allow_credentials stays False, so this policy never lets another origin send the
#   jp_auth cookie — cross-origin requests can only authenticate with the explicit
#   x-jobpilot-key header, which the extension sets and a random page does not.
#
#   the methods and headers are named, not "*". A wildcard invites any extension to
#   send anything; the app only needs these. The real defence is still the auth gate
#   on the server — CORS is a browser-side courtesy, not a lock — but there is no
#   reason to hold the door wider than the extension uses.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://[a-p]{32}|moz-extension://[0-9a-f-]{36}",
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "x-jobpilot-key"],
)


# ── A lock on the door ─────────────────────────────────────────────────────────
#
# This app has a person's phone number and home address in it, and a reset endpoint
# that empties the database with no confirmation. On a public URL, the URL is not a
# secret — bots sweep the whole internet — so "nobody knows the address" is not a
# defence.
#
# Cloudflare Access sits in front of this and asks for a Google login before a
# request ever reaches here. This is the second lock, in case Access is ever
# misconfigured or the tunnel is pointed straight at the app: set JOBPILOT_PASSWORD
# and every request must carry it.
#
# Unset, the gate is open — so local development, where the app is only ever on
# localhost, is unchanged. It is the deploy step that sets the password.
def _password() -> str:
    """Read the password at request time, not import time.

    Bound at import, the gate would freeze whatever the environment held when
    src.api was first imported — making it depend on import order and forcing a test
    that sets the password to reload the whole module. Reading it per request costs
    nothing and always reflects the environment as it is."""
    return os.environ.get("JOBPILOT_PASSWORD", "").strip()

# The extension calls the API from a chrome-extension:// origin and cannot carry a
# cookie, so it authenticates with the same password as a header instead.
_OPEN_PATHS = ("/api/health", "/healthz", "/api/version")


@app.middleware("http")
async def _gate(request: Request, call_next):
    # The audit found the previous version of this backwards, and it is worth writing
    # down so it does not come back.
    #
    # It trusted "the request arrived on localhost" as proof the request was local.
    # But cloudflared connects to the app ON localhost: every tunnel request arrives
    # from 127.0.0.1. So the rule was not "trust the extension", it was "trust
    # anything coming through the tunnel" — the exact opposite of the point. Stripping
    # one forwarded header opened the gate onto /api/maint/reset.
    #
    # There is no network fact that distinguishes the owner from an attacker here;
    # both are on the far side of the same tunnel. Only the password does. So no IP is
    # trusted. Proof is the cookie (a browser that logged in) or the header (the
    # extension, carrying the key on every call). Nothing else gets in.
    password = _password()
    if not password:
        return await call_next(request)

    path = request.url.path

    # A tiny, fixed set of endpoints that must answer before login: the health check
    # a monitor hits, and the login form and its POST.
    if path in _OPEN_PATHS or path in ("/login", "/api/login"):
        return await call_next(request)

    cookie = request.cookies.get("jp_auth", "")
    header = request.headers.get("x-jobpilot-key", "")
    if secrets.compare_digest(cookie, password) or \
       secrets.compare_digest(header, password):
        return await call_next(request)

    # A browser asking for a page gets the login screen; anything else gets a 401.
    if path.startswith("/api/"):
        return Response('{"detail":"unauthorized"}', status_code=401,
                        media_type="application/json")
    return Response(_LOGIN_HTML, status_code=401, media_type="text/html")


_LOGIN_HTML = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>JobPilot</title>
<style>body{font-family:system-ui;background:#0f1115;color:#e7e7e7;display:grid;
place-items:center;height:100vh;margin:0}form{background:#1a1d24;padding:2rem;
border-radius:12px;width:min(90vw,320px)}h1{font-size:1rem;margin:0 0 1rem}
input{width:100%;box-sizing:border-box;padding:.7rem;border-radius:8px;border:1px
solid #333;background:#0f1115;color:#e7e7e7;margin-bottom:.8rem}button{width:100%;
padding:.7rem;border:0;border-radius:8px;background:#c9a227;color:#0f1115;
font-weight:600;cursor:pointer}</style></head><body>
<form method=post action=/api/login>
<h1>JobPilot</h1>
<input type=password name=password placeholder=Password autofocus>
<button>Enter</button></form></body></html>"""


@app.post("/api/login")
async def _login(request: Request):
    form = await request.form()
    password = _password()
    if password and secrets.compare_digest(str(form.get("password", "")), password):
        r = Response(status_code=303)
        r.headers["Location"] = "/"
        # Session cookie, http-only, and marked secure so it only ever travels over
        # HTTPS — which the tunnel always is.
        r.set_cookie("jp_auth", password, httponly=True, secure=True,
                     samesite="lax", max_age=60 * 60 * 24 * 30)
        return r
    r = Response(_LOGIN_HTML, status_code=401, media_type="text/html")
    return r




# ---- pipeline run state (in-memory) ----

@app.get("/api/version")
def version():
    """What's running. Handy when a deployment might be behind the repo."""
    return {"version": __version__}


@app.get("/api/health")
def source_health(conn=Depends(_db_dep)):
    rows = conn.execute(
        "SELECT name, ats, fetched, kept, status, error, last_run "
        "FROM source_health ORDER BY status DESC, fetched DESC"
    ).fetchall()
    return [dict(r) for r in rows]



@app.on_event("startup")
def _startup():
    # Bring the database up to date before anything can query it.
    #
    # Every schema change used to need `python data/init_db.py` run by hand, and
    # forgetting it did not produce a helpful message — it produced a 500 from
    # deep inside a query, on whichever endpoint happened to touch the new column
    # first. The app appeared to be broken rather than out of date.
    #
    # The migration is idempotent and takes milliseconds on an up-to-date
    # database, so there is no reason not to simply do it. `init_db.py` still
    # exists for a fresh clone.
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from data.init_db import main as migrate
        migrate()
    except Exception as e:
        # Never take the server down over this — a failed migration should be
        # loud, not fatal. The endpoints that need the new columns will fail
        # clearly, and the reason is right here in the log.
        log.error("[startup] schema migration failed: %s", e)

    scheduler.start()


# ───────────────────────── pipeline runs ─────────────────────────


# ───────────────────────── schedule config ─────────────────────────

# ───────────────────────── sources (companies-backup.yaml) ─────────────────────────

# ───────────────────────── profile.yaml ─────────────────────────

# ── AI features (scrape-time scoring / on-demand generation) ────────────────

# ── Connection tests ────────────────────────────────────────────────────────


# ── Storage & cleanup ───────────────────────────────────────────────────────


# ── Autofill (browser extension) ────────────────────────────────────────────

# ── Importing jobs from outside the fetch pipeline ──────────────────────────

# ── Feedback loop ───────────────────────────────────────────────────────────

# ── Route modules ──
# Routes live in src/routes/*.py as APIRouters and are included here. This block sits
# just above the static mount because the mount catches "/" for the frontend and must
# be registered last; every API router has to be included before it.
from src.routes import profile as profile_routes
from src.routes import sources as sources_routes
from src.routes import settings as settings_routes
from src.routes import providers as providers_routes
from src.routes import admin as admin_routes
from src.routes import jobs as jobs_routes
from src.routes import generation as generation_routes
from src.routes import imports as imports_routes
from src.routes import insights as insights_routes
app.include_router(profile_routes.router)
app.include_router(sources_routes.router)
app.include_router(settings_routes.router)
app.include_router(providers_routes.router)
app.include_router(admin_routes.router)
app.include_router(jobs_routes.router)
app.include_router(generation_routes.router)
app.include_router(imports_routes.router)
app.include_router(insights_routes.router)


app.mount("/", StaticFiles(
    directory=str(Path(__file__).parent.parent / "frontend"),
    html=True,
), name="frontend")
