"""Central configuration for JobPilot.

This is the single place to change file locations and tunable defaults.
Every other module imports from here instead of hard-coding paths or magic
numbers, so moving a folder or renaming the database is a one-line edit.

Layout assumed by these paths::

    job-pilot/
    ├── config/     companies.yaml, profile.yaml, profile.example.yaml
    ├── data/       jobpilot.db, schema.sql
    ├── backups/    <name>.yaml.bak   (written automatically before each save)
    ├── src/        application code   (this package)
    └── frontend/   index.html, app.js, styles.css, logo.svg

All paths are absolute and derived from this file's location, so the app runs
correctly no matter what the current working directory is.
"""
from pathlib import Path

# ── Base directories ────────────────────────────────────────────────────────
# ROOT is the project root (the parent of the `src/` package).
ROOT = Path(__file__).resolve().parent.parent

CONFIG_DIR = ROOT / "config"      # user-editable YAML config
DATA_DIR = ROOT / "data"          # database + schema
BACKUP_DIR = ROOT / "backups"     # timestamped .bak copies of config
FRONTEND_DIR = ROOT / "frontend"  # static single-page app

# ── Config files ────────────────────────────────────────────────────────────
COMPANIES_FILE = "companies.yaml"   # names are resolved inside CONFIG_DIR
PROFILE_FILE = "profile.yaml"

# ── Database ────────────────────────────────────────────────────────────────
DB_PATH = str(DATA_DIR / "jobpilot.db")
SCHEMA_PATH = str(DATA_DIR / "schema.sql")

# ── Fetching ────────────────────────────────────────────────────────────────
# Boards are fetched in parallel. They are different hosts doing nothing but
# serving a JSON file, so waiting for them one at a time is wasted wall-clock —
# 70+ sources sequentially is minutes of pure network latency.
#
# Keep this modest: several companies share one ATS host (a dozen sit on
# Greenhouse), and hammering it would be rude and might get us rate-limited.
FETCH_CONCURRENCY = 8

# ── Scoring ─────────────────────────────────────────────────────────────────
# Scoring used to be pinned to local Ollama on the theory that per-run volume
# would burn a free-tier quota. That reasoning no longer holds: once the database
# is warm, a run scores a handful of genuinely new jobs, not hundreds. Cerebras
# alone allows 1M tokens a day.
#
# So scoring now goes through the same provider chain as everything else — much
# faster, and a materially better judgement of fit. Ollama remains in the chain
# as the last resort, so an offline machine still works.
SCORING_VIA_PROVIDER_CHAIN = True

# How many of your own past decisions are shown to the model as calibration.
# Your saved and dismissed jobs are the only ground truth about what you actually
# want — a score the model assigned that you then dismissed is a labelled error,
# and it is free to learn from.
FEEDBACK_SAVED_EXAMPLES = 8
FEEDBACK_DISMISSED_EXAMPLES = 8
FEEDBACK_MIN_EXAMPLES = 3        # below this there is nothing to learn from

# ── Privacy ─────────────────────────────────────────────────────────────────
# Writing a cover letter genuinely requires your background — that is the point of
# it. But it does NOT require your phone number, your home address or your email:
# those only appear in a resume header, and JobPilot can fill them in locally
# after the model has written the document.
#
# So there are three modes, not two:
#
#   "redacted"  (default) — hosted models are used, but your direct identifiers
#                 (name, email, phone, address, profile links) are never put in a
#                 prompt. The model writes around placeholders; JobPilot fills them
#                 in on this machine. The model does see your skills, projects and
#                 employment history — it cannot write about you otherwise.
#
#   "local"     — nothing personal leaves the machine at all. Ollama only, no
#                 fallback. Maximum privacy, weaker writing.
#
#   "full"      — everything, including contact details, goes to the hosted model.
#                 No reason to pick this; it is here so the choice is yours.
PRIVACY_MODE = "redacted"          # "redacted" | "local" | "full"

# The fields that must never appear in a prompt in redacted mode. They are
# substituted back in locally, after the model has finished.
REDACTED_FIELDS = ("name", "first_name", "last_name", "email", "phone",
                   "address", "postal_code", "linkedin", "github", "website")

# Follow a job link to recover its description, even when the link is a tracking
# redirect from an alert email.
#
# The honest reckoning: you are going to click that link yourself to read the job,
# and the moment you do, the sender learns the same thing from the same machine.
# Fetching it here is not a new exposure — but it does mean the request happens
# whether or not you ever open the mail. Turn it off if that distinction matters
# to you; the cost is that jobs from alert emails stay unscored.
FOLLOW_JOB_LINKS = True

# Emails you hand to JobPilot land here. Nothing reads your mailbox — you export
# the alert emails you want (Save as .eml / .html) and drop them in.
MAIL_DROP_DIR = ROOT / "data" / "mail_drop"

# ── Files surfaced in the Settings > Configuration files card ────────────────
# The UI lists these so you know exactly what to edit and where it lives.
ENV_FILE = ROOT / ".env"

CONFIG_FILES = [
    {
        "label": "Profile (career database)",
        "description": "Your skills, experience, projects, education.",
        "path": "config/profile.yaml",
    },
    {
        "label": "Companies & sources",
        "description": "Which boards to fetch, filters, active toggles.",
        "path": "config/companies.yaml",
    },
    {
        "label": "Resume template",
        "description": "Markdown structure the AI fills per job.",
        "path": "config/resume_template.md",
    },
    {
        "label": "Environment variables",
        "description": "API keys (Gemini, Cerebras, Telegram).",
        "path": ".env",
    },
]

# ── Scoring model (Ollama) ──────────────────────────────────────────────────
# PRIMARY is the default; the pipeline falls back to FALLBACK for any single
# job that fails on the primary (e.g. out-of-memory), then resets next run.
MODEL_PRIMARY = "qwen2.5:14b"
MODEL_FALLBACK = "qwen2.5:7b"
MODEL_NUM_CTX = 4096         # context window passed to Ollama
MODEL_TEMPERATURE = 0        # deterministic scoring

# ── Pipeline defaults (overridable from the Settings tab / DB) ───────────────
DEFAULT_SCORE_THRESHOLD = 70   # feed shows jobs at or above this
NOTIFY_MIN_SCORE = 60          # jobs at/above this are listed in run summaries
DEFAULT_RUN_INTERVAL_HOURS = 8 # auto-fetch cadence
SCHEDULER_POLL_SECONDS = 60    # how often the scheduler checks if a run is due

# ── Weighted composite for the overall score ────────────────────────────────
# overall = round(SKILLS*w1 + SENIORITY*w2 + DOMAIN*w3); weights sum to 1.0.
SCORE_WEIGHT_SKILLS = 0.5
SCORE_WEIGHT_SENIORITY = 0.3
SCORE_WEIGHT_DOMAIN = 0.2


# ── Application-document generation (cover letter / resume tailoring) ─────────
# The generator walks LLM_PROVIDER_ORDER and uses the first provider that is
# enabled, configured (API key present) and responds successfully. Ollama is the
# always-available local fallback, so the feature works with no keys at all.
#
# The order and the enabled/disabled state can be changed from the UI (they are
# stored in the settings table); the values here are the defaults.
LLM_PROVIDER_ORDER = ["gemini", "cerebras", "ollama"]

# Registry: everything the UI needs to render a provider, and the client needs
# to call it. Add a provider here and it appears in the panel automatically.
#   env            : environment variable holding the key (None = local, no key)
#   daily_tokens   : free-tier daily token allowance, for the usage bar
#                    (None = no quota to track, i.e. local)
LLM_PROVIDERS = {
    "gemini": {
        "label": "Google Gemini",
        "model": "gemini-2.5-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env": "GEMINI_API_KEY",
        "daily_tokens": 1_000_000,
        "note": "Gemini 2.5 Flash · free tier · 1M token context",
    },
    "cerebras": {
        "label": "Cerebras",
        "model": "llama-3.3-70b",
        "base_url": "https://api.cerebras.ai/v1",
        "env": "CEREBRAS_API_KEY",
        "daily_tokens": 1_000_000,
        "note": "Llama 3.3 70B · 1M tokens/day free · ultra-fast",
    },
    "ollama": {
        "label": "Ollama (local)",
        "model": MODEL_PRIMARY,
        "base_url": None,
        "env": None,
        "daily_tokens": None,          # local — no quota
        "note": "Runs on your machine · unlimited · always the last resort",
    },
}

LLM_TEMPERATURE = 0.4          # enough variety for natural prose, still grounded
LLM_TIMEOUT_SECONDS = 60

COVER_LETTER_WORDS = 280       # target length; the model aims for ~this

# Only the most recent projects are considered when picking what to feature.
# The profile lists projects newest-first, so this takes the top N.
COVER_LETTER_PROJECT_POOL = 4   # how many recent projects are ranked
COVER_LETTER_PROJECTS_USED = 2  # how many make it into the letter

# Every letter states willingness to relocate for the role.
COVER_LETTER_MENTION_RELOCATION = True

# After the first draft the model critiques its own letter against a rubric and
# rewrites it. Costs one extra call, but is the single biggest quality gain.
COVER_LETTER_REVISE = True

# ── Resume tailoring ────────────────────────────────────────────────────────
# The AI fills this Markdown template per job: same structure every time, only
# the content changes. Edit the template to change your resume's shape.
RESUME_TEMPLATE_FILE = "resume_template.md"     # lives in config/
RESUME_PROJECT_POOL = 4      # how many recent projects are ranked
RESUME_PROJECTS_USED = 3     # how many make it into the resume

# ── Project selection ────────────────────────────────────────────────────────
# The profile usually lists more projects than belong on one application, and
# stale work is rarely the best evidence. So we first narrow to the most recent
# PROJECT_POOL_SIZE projects, then rank *those* for relevance to the job and put
# the best PROJECTS_IN_LETTER of them in the letter.
#
# "Most recent" = sorted by each project's optional `date` field (newest first).
# Projects with no date keep their profile.yaml order, which is assumed to be
# newest-first, as on a resume.
PROJECT_POOL_SIZE = 4          # how many recent projects are even considered
PROJECTS_IN_LETTER = 2         # how many make it into the letter
