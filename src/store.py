"""SQLite persistence: seen-log dedup + kept jobs."""
import sqlite3

DB = "jobpilot.db"


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
    conn.execute(
        """INSERT OR IGNORE INTO jobs
           (dedupe_hash, source, source_url, apply_url, title, company,
            location, remote, description, posted_date, score, skills_score,
            seniority_score, domain_score, rationale, flags)
           VALUES (:dedupe_hash, :source, :source_url, :apply_url, :title,
                   :company, :location, :remote, :description, :posted_date,
                   :score, :skills_score, :seniority_score, :domain_score,
                   :rationale, :flags)""",
        job,
    )