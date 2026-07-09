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
