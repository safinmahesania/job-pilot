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
    deadline        TEXT
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