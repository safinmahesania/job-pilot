# JobPilot

A zero-cost, self-hosted job-search engine. It pulls postings straight from
company applicant-tracking systems (Greenhouse, Lever, Ashby, Workday, Oracle,
Phenom) and remote-job portals, filters them against your profile, scores each
one with a **local** LLM, and serves a ranked feed you can triage from a browser
— desktop or phone.

Built for Canadian junior / intern / entry-level IT roles, but the profile and
sources are fully configurable.

## Why

Job boards bury the few roles that fit you under thousands that don't. JobPilot
reads company boards directly (no aggregator spam), applies your hard constraints
(location, level, domain), then uses a local model to rate fit — so the top of
your feed is genuinely worth applying to. Everything runs on your machine at no
cost: local model, local database, free APIs.

## How it works

```
fetch (adapters)  ->  normalise + dedupe  ->  prefilter (location / level / domain)
      ->  AI score (Ollama, 0-100)  ->  store  ->  ranked feed in the browser
```

- **Fetch** — one adapter per ATS; each company is a config entry with a board token.
- **Prefilter** — cheap rule checks drop most postings before the model runs.
- **Score** — a local LLM (qwen2.5) rates skills / seniority / domain fit and writes a one-line rationale.
- **Serve** — a FastAPI backend plus a single-page frontend (Alpine.js + Tailwind, no build step).

## Tech stack

Python · FastAPI · SQLite · Ollama (qwen2.5) · Alpine.js · Tailwind (CDN)

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) running locally with a model pulled:
  ```bash
  ollama pull qwen2.5:14b     # or qwen2.5:7b for lower memory use
  ```

## Setup

```bash
# 1. install dependencies
pip install -r requirements.txt

# 2. configure
cp .env.example .env                        # optional: Telegram notifications
cp config/profile.example.yaml config/profile.yaml     # then edit with your details
cp config/companies.example.yaml config/companies.yaml # then choose your boards

# 3. create the database
python data/init_db.py

# 4. run the pipeline once
python -m src.run

# 5. start the app
uvicorn src.api:app --reload
#    open http://localhost:8000
```

All commands are run from the project root.

## Configuration

Everything tunable lives in two places:

- **`config/profile.yaml`** — your skills, experience, projects, and search
  filters (locations, role levels, domains, exclude keywords). The scorer reads
  this on every run; it's also editable from the **Profile** tab in the UI.
- **`config/companies.yaml`** — the boards to fetch. Each entry has an `ats` and
  an identifier (a board token, or tenant/host/site for Workday). Toggle boards
  on and off from the **Sources** tab. This file is gitignored (it's your personal
  source list); `companies.example.yaml` documents the format for every ATS.

Code-level defaults (model names, score weights, the feed threshold, scheduler
cadence, file locations) all live in a single file: **`src/paths.py`**. Change a
path or a default there and every module picks it up — no hunting through the
codebase.

## Project layout

```
config/                 user-editable YAML
  companies.yaml         boards to fetch (gitignored)
  companies.example.yaml boards template
  profile.yaml           your profile (gitignored)
  profile.example.yaml   profile template

data/
  schema.sql            database schema
  init_db.py            creates the database
  jobpilot.db           local SQLite data (gitignored)

src/
  paths.py              ← central paths + tunable constants (edit here)
  api.py                FastAPI backend; also serves the frontend
  run.py                the pipeline (fetch -> filter -> score -> store)
  scheduler.py          in-app periodic runs
  normalize.py          cleaning + dedupe hashing
  store.py              SQLite access
  config.py             loads companies.yaml / profile.yaml
  configio.py           reads/writes config from the UI (with backups)
  maintenance.py        rescore, cleanup, export, reset
  notify.py             Telegram run summaries
  report.py             CLI report of the current feed
  adapters/             one module per ATS (greenhouse, lever, ashby, workday, ...)
  scoring/
    prefilter.py        rule-based filtering
    rerank.py           LLM scoring (with automatic 7b fallback)

frontend/               index.html, app.js, styles.css, logo.svg
scripts/                standalone tools (see below)
  seeds/                company lists + harvest output
backups/                automatic .bak copies of config (gitignored)
```

## Scripts

Run from the project root:

- `python scripts/detect_ats.py` — detect a company's ATS + board token from its careers page
- `python scripts/harvest_board_tokens.py` — guess and verify board tokens for many companies at once
- `python scripts/purge_filtered_jobs.py` — re-run the prefilter over stored jobs and dismiss failures
- `python scripts/show_top_feed.py` — print the current top-scored feed

## Notes

- The feed **accumulates** — don't wipe the database between runs; dedupe handles
  repeats, and re-running only scores genuinely new postings.
- Scoring is the only step that needs the GPU. If a job runs out of memory on the
  primary model, that single job falls back to the smaller model and the run
  continues.

## Cover letters

Open any job (click its title) and hit **Cover letter**. JobPilot generates a
grounded draft using your profile and the job description, following one fixed
professional structure — only the content changes per application. It picks the
most relevant of your projects for the role and does not invent facts.

Generation tries, in order: **Gemini** (free tier, best quality) → **Cerebras**
(free, fast) → **local Ollama** (always available). Add `GEMINI_API_KEY` and/or
`CEREBRAS_API_KEY` to `.env` for the hosted options; with no keys it runs fully
local. All provider defaults live in `src/paths.py`.

## License

[MIT](LICENSE).
