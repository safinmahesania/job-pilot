"""The core pipeline: fetch -> normalise -> prefilter -> AI score -> store.

Run it directly with ``python -m src.run`` for a one-off pass, or let the
in-app scheduler (``src.scheduler``) trigger it periodically. Each pass records
a row in the ``runs`` table and, if configured, sends a Telegram summary.

Boards are fetched in parallel and jobs are processed serially. That split is
deliberate: fetching is 70-odd different hosts serving a JSON file, where waiting
one at a time is minutes of pure network latency for nothing — while processing
writes to SQLite, which has a single writer, and is bottlenecked on the model
anyway.
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from src.config import load_companies, load_profile
from src.adapters.base import get_adapter
from src.normalize import normalize, is_valid
from src.scoring.prefilter import passes
from src.scoring.rerank import (
    score_job, reset_model_state, set_preferred, get_model_state,
    build_calibration,
)
from src import store, notify
from src.paths import DEFAULT_SCORE_THRESHOLD, NOTIFY_MIN_SCORE, FETCH_CONCURRENCY
from src.logs import log


def _fetch_one(company: dict) -> tuple[dict, list[dict], dict]:
    """Fetch one board. Returns (company, raw_jobs, health).

    Runs on a worker thread, so it touches no shared state and no database. A
    failing board is recorded and returned — never raised into the pool, where it
    would take the run down with it.
    """
    health = {"fetched": 0, "kept": 0, "status": "ok", "error": None}
    try:
        raw_jobs = get_adapter(company).fetch()
        health["fetched"] = len(raw_jobs)
        return company, raw_jobs, health
    except Exception as e:
        log.warning("[%s] fetch failed: %s", company["name"], e)
        health["status"] = "error"
        health["error"] = str(e)[:200]
        return company, [], health


def fetch_all(companies: list[dict]) -> list[tuple[dict, list[dict], dict]]:
    """Fetch every active board at once.

    Concurrency is capped (FETCH_CONCURRENCY) because a dozen of our companies
    share a single ATS host — hammering it would be both rude and likely to earn
    a rate limit. With the cap, a run takes about as long as the slowest board
    rather than the sum of all of them.
    """
    active = [c for c in companies if c.get("active")]
    if not active:
        return []

    results = []
    with ThreadPoolExecutor(max_workers=FETCH_CONCURRENCY) as pool:
        futures = {pool.submit(_fetch_one, c): c for c in active}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:                  # the worker itself blew up
                company = futures[future]
                log.warning("[%s] worker failed: %s", company["name"], e)
                results.append((company, [], {"fetched": 0, "kept": 0,
                                              "status": "error",
                                              "error": str(e)[:200]}))
    return results


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

    # Scrape-time AI: when off, jobs are still fetched, filtered and stored — they
    # just aren't scored (they land unscored instead of in the ranked feed).
    scoring_on = store.get_setting(conn, "scoring_enabled", "1") == "1"

    stats = {"fetched": 0, "seen": 0, "dropped": 0, "trashed": 0, "kept": 0,
             "errors": 0}
    seen_this_run = set()           # guards against duplicates within one pass
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Your own past decisions, shown to the model as calibration. Built once for
    # the run: it needs a database read, and it is identical for every job.
    calibration = build_calibration() if scoring_on else ""
    if calibration:
        print("  scoring is calibrated against your saved/dismissed history")

    # ── Fetch every board in parallel ───────────────────────────────────────
    fetch_started = time.time()
    fetched = fetch_all(companies)
    print(f"  fetched {len(fetched)} sources in {time.time() - fetch_started:.1f}s")

    # ── Then process serially ───────────────────────────────────────────────
    for company, raw_jobs, src_stat in fetched:
        if src_stat["status"] == "error":
            stats["errors"] += 1
            store.save_source_health(conn, company["name"], company.get("ats"),
                                     src_stat, now)
            # A board that failed to fetch is worth keeping too — not raised (the
            # pool already swallowed it so one broken board cannot stop the run), but
            # recorded, so "why did nothing come from talent.com last night" has an
            # answer.
            store.record_source_error(
                conn, f"fetch:{company['name']}",
                src_stat.get("error") or "fetch failed")
            continue

        for raw in raw_jobs:
            stats["fetched"] += 1
            job = normalize(raw)
            h = job["dedupe_hash"]

            # Skip anything already processed, this run or a previous one.
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

            # The expensive step (skipped when scrape-time AI is off).
            if not scoring_on:
                job.update(score=None, skills_score=None, seniority_score=None,
                           domain_score=None, rationale=None, flags=None)
                store.save_job(conn, job)
                store.mark_seen(conn, h, "kept")
                stats["kept"] += 1
                src_stat["kept"] += 1
                continue

            result = score_job(job, profile, calibration)
            if result is None:
                stats["errors"] += 1
                continue

            # Persist every scored job; the feed filters by threshold at read time.
            job.update(score=result.overall, skills_score=result.skills_score,
                       seniority_score=result.seniority_score,
                       domain_score=result.domain_score,
                       rationale=result.rationale, flags=None)
            store.save_job(conn, job)

            threshold = int(store.get_setting(conn, "score_threshold",
                                              DEFAULT_SCORE_THRESHOLD))
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

        store.save_source_health(conn, company["name"], company.get("ats"),
                                 src_stat, now)

    # Record this run in history.
    conn.execute(
        "INSERT INTO runs (kind, fetched, seen, dropped, trashed, kept, errors) "
        "VALUES ('fetch', ?, ?, ?, ?, ?, ?)",
        (stats["fetched"], stats["seen"], stats["dropped"],
         stats["trashed"], stats["kept"], stats["errors"]),
    )
    conn.commit()
    conn.close()

    # Telegram summary (a no-op if not configured or disabled).
    new_scored.sort(key=lambda x: -x["score"])
    notify.send(notify.run_summary(stats, time.time() - start_ts,
                                   get_model_state()["active"], new_scored))

    reset_model_state()             # leave the UI showing the preferred model

    print("\n=== Run summary ===")
    for k, v in stats.items():
        print(f"  {k:10} {v}")
    print(f"  {'elapsed':10} {time.time() - start_ts:.1f}s")


if __name__ == "__main__":
    run()
