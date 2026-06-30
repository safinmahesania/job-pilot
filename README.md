# Job Matcher

Phase 1: ingest jobs from company ATS feeds, score them against a profile,
and print a ranked shortlist with reasons. No UI yet — the goal is to prove
the matching works before building anything else.

## Setup
1. `cp .env.example .env` and fill in keys
2. `cp profile.example.yaml profile.yaml` and fill in your profile
3. `python3 -m venv .venv && source .venv/bin/activate`
4. `pip install -r requirements.txt`
5. `python -m src.run`

## Layout
- `companies.yaml`    — registry of companies + their ATS type (committed)
- `profile.yaml`      — your profile (gitignored; copy from the example)
- `src/adapters/`     — one fetcher per source, behind a shared interface
- `src/normalize.py`  — maps raw records into the common job schema
- `src/scoring/`      — prefilter -> embed -> LLM rerank
- `src/store.py`      — Supabase/Postgres persistence
- `src/run.py`        — entry point that ties it all together

## Phase 1 done when
It runs end to end on one source, prints a ranked list with reasons, and on
the top 10 you'd genuinely apply to 6 or more.
