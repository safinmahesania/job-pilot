"""SQLite persistence: seen-log dedup + kept jobs."""
import sqlite3

from src.paths import DB_PATH as DB


def connect():
    return sqlite3.connect(DB)


def already_seen(conn, dedupe_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM seen WHERE dedupe_hash = ?", (dedupe_hash,)
    ).fetchone()
    return row is not None


def mark_seen(conn, dedupe_hash: str, decision: str, score: float | None = None):
    conn.execute(
        "INSERT OR IGNORE INTO seen (dedupe_hash, decision, score) VALUES (?, ?, ?)",
        (dedupe_hash, decision, score),
    )


def save_job(conn, job: dict):
    # job_type, deadline and the salary pair used to be missing from this list, so
    # adapters filled them and the columns stayed NULL — which quietly disabled
    # profile.yaml's `salary_floor` filter. Anything the schema stores must be
    # named here.
    conn.execute(
        """INSERT OR IGNORE INTO jobs
           (dedupe_hash, source, source_url, apply_url, title, company,
            location, remote, description, posted_date, score, skills_score,
            seniority_score, domain_score, rationale, flags,
            job_type, deadline, salary_min, salary_max)
           VALUES (:dedupe_hash, :source, :source_url, :apply_url, :title,
                   :company, :location, :remote, :description, :posted_date,
                   :score, :skills_score, :seniority_score, :domain_score,
                   :rationale, :flags,
                   :job_type, :deadline, :salary_min, :salary_max)""",
        job,
    )


def save_source_health(conn, name, ats, stat, when):
    conn.execute(
        """INSERT INTO source_health (name, ats, fetched, kept, status, error, last_run)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET
             ats=excluded.ats, fetched=excluded.fetched, kept=excluded.kept,
             status=excluded.status, error=excluded.error, last_run=excluded.last_run""",
        (name, ats, stat["fetched"], stat["kept"], stat["status"], stat["error"], when),
    )
    conn.commit()


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()
