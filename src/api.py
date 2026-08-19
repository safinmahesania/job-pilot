"""FastAPI backend for JobPilot — serves jobs, status updates, and the frontend."""
from pathlib import Path
import os
from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, FileResponse
from src import scheduler
from src.deps import (_db_dep)
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
    allow_headers=["Content-Type", "Authorization"],
)


# ── Public config for the browser client ──────────────────────────────────────
#
# Auth is Supabase now: every /api/* route verifies a Bearer JWT (see src/auth.py
# and the current_user_id dependency), so there is no server-side password gate any
# more. The frontend and the extension sign in with Supabase and send the token on
# each call. Static files and this one endpoint answer without a token, so the login
# screen can load and bootstrap the client.
@app.get("/api/public-config")
def public_config():
    """The public Supabase settings the browser needs to start its auth client.

    Both values are public by design — the anon key is meant to ship to browsers;
    row-level security and JWT verification are what actually protect the data."""
    return {
        "supabase_url": os.environ.get("SUPABASE_URL", ""),
        "supabase_anon_key": os.environ.get("SUPABASE_ANON_KEY", ""),
    }


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
    # Schema is managed in Supabase (Postgres) now, not by a local SQLite migration.
    # The old startup step ran data/init_db.py to bring a SQLite file up to date;
    # under Postgres the schema is applied once in the Supabase SQL editor, so there
    # is nothing to migrate here on boot.
    scheduler.start()


# ───────────────────────── pipeline runs ─────────────────────────


# ───────────────────────── schedule config ─────────────────────────

# ───────────────────────── sources (companies.yaml) ─────────────────────────

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
from src.routes import dashboard as dashboard_routes
app.include_router(profile_routes.router)
app.include_router(sources_routes.router)
app.include_router(settings_routes.router)
app.include_router(providers_routes.router)
app.include_router(admin_routes.router)
app.include_router(jobs_routes.router)
app.include_router(generation_routes.router)
app.include_router(imports_routes.router)
app.include_router(insights_routes.router)
app.include_router(dashboard_routes.router)


# ── Client-side routes ──────────────────────────────────────────────────────
# The frontend is a single-page app: these paths all serve the same index.html and
# the app reads the URL to decide which page to show. This is what makes /profile,
# /sources, etc. work on a direct visit or a reload (not just via in-app navigation).
# Registered before the "/" mount below so they take priority over static serving.
_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
_SPA_PATHS = [
    "feed", "unscored", "saved", "applied", "dismissed", "profile",
    "sources", "stats", "import", "settings", "admin",
]


@app.get("/{page}")
def spa_page(page: str):
    """Serve the SPA shell for a known client-side route; 404 otherwise so real
    missing files still error normally."""
    if page in _SPA_PATHS:
        return FileResponse(_FRONTEND_DIR / "index.html")
    # Not a known page — let it fall through to static files (or 404).
    target = _FRONTEND_DIR / page
    if target.is_file():
        return FileResponse(target)
    return Response(status_code=404)


app.mount("/", StaticFiles(
    directory=str(Path(__file__).parent.parent / "frontend"),
    html=True,
), name="frontend")
