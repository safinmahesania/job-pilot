"""Maintenance operations: rescore, cleanup, export, reset, etc."""
import csv
import io
import os
import shutil
from datetime import datetime, timedelta

from src import store
from src.config import load_companies, load_profile


def rescore_all():
    """Re-run AI scoring on all stored jobs with current profile + threshold."""
    from src.scoring.rerank import score_job
    profile = load_profile()
    conn = store.connect()
    threshold = int(store.get_setting(conn, "score_threshold", 70))
    rows = conn.execute("SELECT id, title, company, location, description, job_type FROM jobs").fetchall()
    updated = 0
    for jid, title, company, location, desc, jtype in rows:
        job = {"title": title, "company": company, "location": location,
               "description": desc, "job_type": jtype}
        r = score_job(job, profile)
        if r is None:
            continue
        conn.execute(
            "UPDATE jobs SET score=?, skills_score=?, seniority_score=?, domain_score=?, rationale=? WHERE id=?",
            (r.overall, r.skills_score, r.seniority_score, r.domain_score, r.rationale, jid),
        )
        # feed membership refresh: below threshold + still surfaced -> dismissed? no, keep as-is
        updated += 1
    conn.commit()
    conn.close()
    return {"rescored": updated}


def cleanup_below_threshold():
    """Archive (dismiss) surfaced jobs scoring below current threshold."""
    conn = store.connect()
    threshold = int(store.get_setting(conn, "score_threshold", 70))
    cur = conn.execute(
        "UPDATE jobs SET status='dismissed' WHERE status='surfaced' AND score < ?",
        (threshold,),
    )
    conn.commit()
    n = cur.rowcount
    conn.close()
    return {"archived": n}


def clear_old_jobs(days: int):
    """Permanently delete jobs older than N days (by fetched_at)."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = store.connect()
    cur = conn.execute("DELETE FROM jobs WHERE fetched_at < ?", (cutoff,))
    conn.commit()
    n = cur.rowcount
    conn.close()
    return {"deleted": n}


def export_csv() -> str:
    """Return all jobs as a CSV string."""
    conn = store.connect()
    conn.row_factory = None
    cols = ["id", "title", "company", "location", "job_type", "source",
            "apply_url", "score", "status", "posted_date", "deadline", "rationale"]
    rows = conn.execute(f"SELECT {','.join(cols)} FROM jobs ORDER BY score DESC").fetchall()
    conn.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    w.writerows(rows)
    return buf.getvalue()


def reload_config():
    """Force re-read of profile.yaml + companies.yaml (validates them)."""
    p = load_profile()
    c = load_companies()
    return {"profile_ok": bool(p), "companies": len(c)}


def clean_cache():
    """Delete __pycache__ dirs. Jobs/data untouched."""
    removed = 0
    for root, dirs, _ in os.walk("."):
        for d in list(dirs):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                removed += 1
    return {"removed_dirs": removed}


def reset_all_jobs():
    """DESTRUCTIVE: wipe jobs + seen tables. Config/settings preserved."""
    conn = store.connect()
    conn.execute("DELETE FROM jobs")
    conn.execute("DELETE FROM seen")
    conn.execute("DELETE FROM source_health")
    conn.commit()
    conn.close()
    return {"reset": True}