"""The core pipeline: fetch -> normalise -> prefilter -> AI score -> store.

Run it directly with ``python -m src.run`` for a one-off pass, or let the
in-app scheduler (``src.scheduler``) trigger it periodically. Each pass records
a row in the ``runs`` table and, if configured, sends a Telegram summary.
"""
import time
from datetime import datetime

from src.config import load_companies, load_profile
from src.adapters.base import get_adapter
from src.normalize import normalize, is_valid
from src.scoring.prefilter import passes
from src.scoring.rerank import (
    score_job, reset_model_state, set_preferred, get_model_state,
)
from src import store, notify
from src.paths import DEFAULT_SCORE_THRESHOLD, NOTIFY_MIN_SCORE


def run():
    start_ts = time.time()
    new_scored = []                 # jobs worth surfacing in the run summary

    profile = load_profile()
    companies = load_companies()
    conn = store.connect()

    # Scoring model: honour the saved preference, but always start the run on it
    # (a per-job fallback may switch to the smaller model mid-run; reset undoes it).
    saved_model = store.get_setting(conn, "scoring_model", None)
    if saved_model:
        set_preferred(saved_model)
    reset_model_state()

    stats = {"fetched": 0, "seen": 0, "dropped": 0, "trashed": 0, "kept": 0, "errors": 0}
    seen_this_run = set()           # guards against duplicates within one pass
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    for company in companies:
        if not company.get("active"):
            continue

        src_stat = {"fetched": 0, "kept": 0, "status": "ok", "error": None}
        try:
            raw_jobs = get_adapter(company).fetch()
        except Exception as e:
            print(f"[{company['name']}] fetch failed: {e}")
            stats["errors"] += 1
            src_stat["status"] = "error"
            src_stat["error"] = str(e)[:200]
            store.save_source_health(conn, company["name"], company.get("ats"), src_stat, now)
            continue

        for raw in raw_jobs:
            stats["fetched"] += 1
            src_stat["fetched"] += 1
            job = normalize(raw)
            h = job["dedupe_hash"]

            # Skip anything we've already processed, this run or a previous one.
            if store.already_seen(conn, h) or h in seen_this_run:
                stats["seen"] += 1
                continue
            seen_this_run.add(h)

            # Cheap rule checks before spending a model call.
            if not is_valid(job):
                store.mark_seen(conn, h, "dropped")
                stats["dropped"] += 1
                continue
            if not passes(job, profile):
                store.mark_seen(conn, h, "dropped")
                stats["dropped"] += 1
                continue

            # Expensive step: score fit with the local LLM.
            result = score_job(job, profile)
            if result is None:
                stats["errors"] += 1
                continue

            # Persist every scored job; the feed filters by threshold at read time.
            job.update(score=result.overall, skills_score=result.skills_score,
                       seniority_score=result.seniority_score,
                       domain_score=result.domain_score,
                       rationale=result.rationale, flags=None)
            store.save_job(conn, job)

            threshold = int(store.get_setting(conn, "score_threshold", DEFAULT_SCORE_THRESHOLD))
            if result.overall >= threshold:
                store.mark_seen(conn, h, "kept", result.overall)
                stats["kept"] += 1
                src_stat["kept"] += 1
                if result.overall >= NOTIFY_MIN_SCORE:
                    new_scored.append({"score": result.overall,
                                       "title": job.get("title"),
                                       "company": job.get("company")})
            else:
                store.mark_seen(conn, h, "trashed", result.overall)
                stats["trashed"] += 1

        store.save_source_health(conn, company["name"], company.get("ats"), src_stat, now)

    # Record this run in history.
    conn.execute(
        "INSERT INTO runs (kind, fetched, seen, dropped, trashed, kept, errors) "
        "VALUES ('fetch', ?, ?, ?, ?, ?, ?)",
        (stats["fetched"], stats["seen"], stats["dropped"],
         stats["trashed"], stats["kept"], stats["errors"]),
    )
    conn.commit()
    conn.close()

    # Telegram summary (no-op if not configured or disabled).
    new_scored.sort(key=lambda x: -x["score"])
    notify.send(notify.run_summary(stats, time.time() - start_ts,
                                   get_model_state()["active"], new_scored))

    reset_model_state()             # leave the UI showing the preferred model

    print("\n=== Run summary ===")
    for k, v in stats.items():
        print(f"  {k:10} {v}")


if __name__ == "__main__":
    run()
