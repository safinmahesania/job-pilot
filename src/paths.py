"""Central configuration for JobPilot.

This is the single place to change file locations and tunable defaults.
Every other module imports from here instead of hard-coding paths or magic
numbers, so moving a folder or renaming the database is a one-line edit.

Layout assumed by these paths::

    job-pilot/
    ├── config/     companies-backup.yaml, profile.yaml, profile.example.yaml
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
COMPANIES_FILE = "companies-backup.yaml"   # names are resolved inside CONFIG_DIR
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

# ── Follow-ups ──────────────────────────────────────────────────────────────
# An unanswered application is usually an email in a queue, not a rejection. A
# polite nudge after a week is the cheapest thing a candidate can do — and the
# thing everyone forgets, because nothing reminds them.
FOLLOWUP_FIRST_DAYS = 7        # applied this long ago, never followed up -> due
FOLLOWUP_SECOND_DAYS = 10      # this long after a follow-up with no reply -> one more
FOLLOWUP_STALE_DAYS = 30       # past this, stop pretending; close it out

# Telegram reminder, at most once a day (the scheduler polls far more often).
FOLLOWUP_NOTIFY = True

# ── Source health ───────────────────────────────────────────────────────────
# A board that returns HTTP 200 and zero jobs is the failure that costs you most,
# because nothing reports it. One empty run means nothing — a company may simply
# have no openings today. A streak means the adapter is broken.
HEALTH_ZERO_STREAK = 3         # empty runs in a row before we call it dead
HEALTH_ERROR_STREAK = 2        # outright failures in a row before we say so
HEALTH_ALERTS = True           # tell you the moment a working board goes dark

# ── Weekly digest ───────────────────────────────────────────────────────────
# Once a week: what you owe, what's waiting, what broke. Monday morning, because
# a digest that arrives on Friday evening gets read on Monday anyway.
WEEKLY_DIGEST = True
WEEKLY_DIGEST_WEEKDAY = 0      # 0 = Monday
WEEKLY_DIGEST_HOUR = 9

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
        "path": "config/companies-backup.yaml",
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
# The feed shows jobs at or above this.
#
# It was 70, and 70 was doing two jobs at once: ranking, and refusing. It had to,
# because the fit check underneath it could not be trusted — a bag-of-words ratio
# that landed every real developer job at 10-18% and matched on the words "what",
# "every" and "day".
#
# The fit check is now a real gate: it refuses any job that names none of the
# languages you write. Sales, recruiting, technical support and network engineering
# all fall to it, cleanly, with no tuning.
#
# So the feed no longer has to be the gate as well, and being one was costing:
#
#     at 70, five real developer jobs were hidden — a DoorDash data scientist role,
#     two Workleap developer roles, a Thinkific data engineer role — all scoring
#     66-68, all matching languages you write, none of which you ever saw.
#
#     at 60, every job the fit check would accept is visible, and the sales posting
#     is still out at 56.
#
# Rank generously. Refuse strictly. They are different jobs.
DEFAULT_SCORE_THRESHOLD = 60
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

# How many bullets of each job survive onto the page.
#
# The profile holds four per job, because that is what the person had to say about
# it. A resume has room for fewer, and the model's job is to pick which ones this
# employer should read — the whole of its job, now that it no longer writes them.
RESUME_EXPERIENCE_BULLETS = 3

# ── Resume length ───────────────────────────────────────────────────────────
# Measured in rendered lines, not characters — src/resume_limits.py converts these
# into character budgets using the real font metrics of the real page, because a
# model can count characters and cannot count lines it has never seen.
#
# These exist to keep the resume to one page. A recruiter spends seconds on it;
# a bullet that runs one line long to say nothing extra costs more than it earns.
RESUME_SUMMARY_LINES = 3            # the whole summary
RESUME_EXPERIENCE_BULLET_LINES = 2  # each bullet, not the section

# The bullets INSIDE each project, in order. Three points per project: two that
# get two lines, and a third that gets one — a short closing line, so the entry
# doesn't trail off in padding. This applies to every project, not across them.
RESUME_PROJECT_BULLET_LINES = (2, 2, 1)

RESUME_VOLUNTEER_LINES = 3          # each entry's description

# ── Fit ─────────────────────────────────────────────────────────────────────
# The fraction of a job's stated requirements that must appear somewhere in your
# profile before a resume will be written for it.
#
# Below this, the honest resume does not exist. Asked to tailor a software
# engineer's resume to a healthcare sales posting, a model writes a resume for a
# salesperson — not out of mischief, but because that is the only answer to the
# question. Refusing is the correct behaviour, and it is also the answer you
# wanted: you cannot honestly apply to this.
#
# 0.15 is deliberately generous. A junior applying to a role wanting five years of
# Kubernetes still shares the vocabulary — languages, tools, practices. A software
# developer and an assisted-living-facility sales rep share almost nothing, which
# is exactly the case this exists to catch.
FIT_MIN_OVERLAP = 0.15

# How many of YOUR technologies a posting must name before a resume for it could be
# honest.
#
# Two, not one. One is a coincidence: an IT operations role mentions "Agile", a sales
# posting mentions "Microsoft Dynamics" and half-matches "Microsoft". Two named tools
# from your own curated list is a job in your field.
#
#     sales / recruiting / sales rep      0
#     IT operations                       1
#     QA (Selenium, Cypress, Playwright)  0
#     TD Bank software engineering        4
#     Geotab software developer           5
#
# The gap is wide enough that this number is not a tuning knob.
FIT_MIN_TECHNOLOGIES = 2

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


# ── Upload size cap ──
# Job import files are CSV/Excel exports and job-alert emails — kilobytes, rarely a
# megabyte. An upload endpoint with no cap will read whatever it is handed straight
# into memory, so a single large POST is an easy denial of service. 10 MB is orders
# of magnitude above any real import and still safe.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


# ── Rate limiting ──
# The generation endpoints (resume, cover letter) and imports each cost an LLM call
# or a file parse, so on a public tunnel they are the expensive surface. A single user
# never hits these fast; a bot or a runaway script can. These are generous ceilings
# that a person will never notice and abuse will.
RATE_LIMIT_GENERATION = "20/minute"   # resume + cover-letter
RATE_LIMIT_IMPORT = "30/minute"       # file / text / email imports
RATE_LIMIT_DEFAULT = "120/minute"     # everything else, as a backstop
