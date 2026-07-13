-- schema.sql  (SQLite — zero cost, local file)

CREATE TABLE IF NOT EXISTS seen (
    dedupe_hash TEXT PRIMARY KEY,
    decision    TEXT NOT NULL,            -- 'dropped' | 'trashed' | 'kept'
    score       REAL,
    first_seen  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_hash     TEXT UNIQUE NOT NULL,
    source          TEXT,
    source_url      TEXT,                 -- the listing/details page
    apply_url       TEXT,                 -- the apply button link
    title           TEXT,
    company         TEXT,
    location        TEXT,
    remote          INTEGER,              -- 0 / 1
    salary_min      INTEGER,
    salary_max      INTEGER,
    description     TEXT,
    posted_date     TEXT,
    fetched_at      TEXT DEFAULT (datetime('now')),
    score           REAL,
    skills_score    REAL,
    seniority_score REAL,
    domain_score    REAL,
    rationale       TEXT,
    flags           TEXT,                 -- JSON string
    status          TEXT DEFAULT 'surfaced',
    job_type        TEXT,
    deadline        TEXT,
    applied_on      TEXT,                 -- date stamped when status -> 'applied'
    notes           TEXT,                 -- free-text notes from the Applied tab
    followed_up_on  TEXT,                 -- date of the last follow-up you sent
    followup_snooze TEXT                  -- don't nag about this until after this date
);

CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score DESC);

CREATE TABLE IF NOT EXISTS source_health (
    name        TEXT PRIMARY KEY,
    ats         TEXT,
    fetched     INTEGER DEFAULT 0,
    kept        INTEGER DEFAULT 0,
    status      TEXT,              -- 'ok' | 'error'
    error       TEXT,              -- error message agar fail hua
    last_run    TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
INSERT OR IGNORE INTO settings (key, value) VALUES ('score_threshold', '70');
INSERT OR IGNORE INTO settings (key, value) VALUES ('scheduler_enabled', '0');
INSERT OR IGNORE INTO settings (key, value) VALUES ('run_interval_hours', '8');
INSERT OR IGNORE INTO settings (key, value) VALUES ('notify_enabled', '1');
INSERT OR IGNORE INTO settings (key, value) VALUES ('scoring_enabled', '1');
INSERT OR IGNORE INTO settings (key, value) VALUES ('generation_enabled', '1');

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT DEFAULT (datetime('now')),
    kind        TEXT,                     -- 'fetch' (reserved for future kinds)
    fetched     INTEGER DEFAULT 0,
    seen        INTEGER DEFAULT 0,
    dropped     INTEGER DEFAULT 0,
    trashed     INTEGER DEFAULT 0,
    kept        INTEGER DEFAULT 0,
    errors      INTEGER DEFAULT 0
);

-- Per-day, per-provider token/request usage for the generation providers.
-- Used by the AI Providers panel to show quota consumption.
CREATE TABLE IF NOT EXISTS llm_usage (
    day       TEXT NOT NULL,          -- UTC date, YYYY-MM-DD
    provider  TEXT NOT NULL,          -- gemini | cerebras | ollama
    tokens    INTEGER DEFAULT 0,
    requests  INTEGER DEFAULT 0,
    PRIMARY KEY (day, provider)
);

-- Generated application documents, bound to the job they were written for.
-- One row per (job, kind): regenerating replaces the previous version, so the
-- material attached to an application is always the current one for THAT job.
CREATE TABLE IF NOT EXISTS materials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL,
    kind        TEXT NOT NULL,           -- 'resume' | 'cover'
    content     TEXT NOT NULL,           -- markdown / plain text
    provider    TEXT,                    -- which model wrote it
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE (job_id, kind),
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
