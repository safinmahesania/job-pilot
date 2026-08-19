"""The core pipeline: fetch -> normalise -> junk-check -> enrich -> extract -> pool.

Run it directly with ``python -m src.run`` for a one-off pass, or let the
in-app scheduler (``src.scheduler``) trigger it periodically. Each pass records
a row in the ``runs`` table and, if configured, sends a Telegram summary.

In the multi-user model this pipeline is ADMIN-side and populates the SHARED
jobs pool only. It scores nothing and drops nothing but true junk — a posting
that is useless to one person may be perfect for another, so per-user filtering
and scoring happen later, when a user builds their feed. Extraction still runs
here (once per posting, globally) so the structured fields are ready for that
per-user filter; it can be turned off for a cheaper fetch and backfilled later.

Boards are fetched in parallel and jobs are processed serially: fetching is 70-odd
hosts serving a JSON file, where waiting one at a time is minutes of network
latency for nothing.
"""
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.config import load_companies
from src.adapters.base import get_adapter
from src.normalize import normalize, is_valid
from src import store, notify, enrich, language, extract
from src.paths import FETCH_CONCURRENCY
from src.logs import log
from src.env import load_env


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


#: What the run is doing right now, read by /api/run/status so the UI can say more
#: than "running". Plain dict rather than a queue: there is one pipeline at a time.
PROGRESS = {"active": False, "phase": "", "source": "",
            "done": 0, "total": 0, "started": 0.0}


def _progress(**kw):
    PROGRESS.update(kw)


def reset_progress():
    PROGRESS.update(active=False, phase="", source="", done=0, total=0, started=0.0)


def fetch_all(companies: list[dict],
              respect_active: bool = True) -> list[tuple[dict, list[dict], dict]]:
    """Fetch every active board at once.

    Concurrency is capped (FETCH_CONCURRENCY) because a dozen companies share a
    single ATS host — hammering it would be both rude and likely to earn a rate
    limit. respect_active=False is for a selective run, where the caller picked the
    exact sources by name and wants them fetched even if their active flag is off.
    """
    active = [c for c in companies if c.get("active")] if respect_active else companies
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


def run(only: list[str] | None = None):
    load_env()
    start_ts = time.time()

    companies = load_companies()

    # Selective run: if `only` is given, fetch just those sources (matched by name),
    # ignoring their active flag — so you can pull from one or two boards on demand.
    if only:
        wanted = {n.strip().lower() for n in only}
        companies = [c for c in companies
                     if (c.get("name") or "").strip().lower() in wanted]
    conn = store.connect()

    # Extraction runs inline unless an admin turns it off (then a backfill fills the
    # structured fields later). It's independent of any per-user scoring.
    extract_on = store.get_setting(conn, "extraction_enabled", "1") == "1"

    stats = {"fetched": 0, "seen": 0, "dropped": 0, "trashed": 0, "kept": 0,
             "errors": 0, "enriched": 0, "enrich_missed": 0, "french_only": 0,
             "extracted": 0}
    seen_this_run = set()           # guards against duplicates within one pass
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── Fetch every board in parallel ───────────────────────────────────────
    fetch_started = time.time()
    _progress(active=True, phase="Fetching sources", source="",
              done=0, total=0, started=fetch_started)
    fetched = fetch_all(companies, respect_active=not only)
    print(f"  fetched {len(fetched)} sources in {time.time() - fetch_started:.1f}s")

    # ── Then process serially ───────────────────────────────────────────────
    _progress(phase="Processing", total=sum(len(r) for _, r, _ in fetched))

    for company, raw_jobs, src_stat in fetched:
        _progress(source=company.get("name", ""))
        if src_stat["status"] == "error":
            stats["errors"] += 1
            with conn:
                store.save_source_health(conn, company["name"], company.get("ats"),
                                         src_stat, now)
                store.record_source_error(
                    conn, f"fetch:{company['name']}",
                    src_stat.get("error") or "fetch failed")
            continue

        for raw in raw_jobs:
            # One transaction per job (not one for the whole run): the run can take
            # minutes and a single long-held write lock would block the UI. `with
            # conn:` commits on a clean exit (including the `continue`s) and rolls
            # back if the body raises, so a job is never half-written.
            with conn:
                _progress(done=PROGRESS["done"] + 1)
                stats["fetched"] += 1
                job = normalize(raw)
                h = job["dedupe_hash"]

                # Skip anything already in the pool, this run or a previous one.
                if store.already_seen(conn, h) or h in seen_this_run:
                    stats["seen"] += 1
                    continue
                seen_this_run.add(h)

                # Drop only true junk — no title / no apply link / no description.
                # Everything else is kept: the pool is shared, and per-user filtering
                # decides relevance later.
                if not is_valid(job):
                    store.mark_seen(conn, h, "dropped")
                    stats["dropped"] += 1
                    continue

                # Pull the full description where the listing only gave a snippet
                # (Adzuna/Lever/Greenhouse links). A complete description helps every
                # user's filter and the extraction below.
                if enrich.is_enrichable(job):
                    if enrich.enrich_if_needed(job):
                        stats["enriched"] += 1
                    else:
                        stats["enrich_missed"] += 1

                # Tag the posting's language (feed can flag French-only roles).
                job["language"] = language.detect(job.get("description"))
                if job["language"] == "fr":
                    stats["french_only"] += 1

                # Structured fields out of the description (work mode, requirements,
                # tech stack…). Independent of scoring; a failure must not lose the
                # job, so it's caught and the fields stay NULL for a later backfill.
                if extract_on:
                    try:
                        ex = extract.extract(job)
                        if ex is not None:
                            job.update(ex.model_dump())
                            job["extracted_at"] = datetime.now().strftime(
                                "%Y-%m-%d %H:%M:%S")
                            stats["extracted"] += 1
                    except Exception as e:
                        store.record_error(conn, f"extract:{company['name']}", e)

                # Into the shared pool. No score, no status — that's per-user.
                store.save_job(conn, job)
                store.mark_seen(conn, h, "kept")
                stats["kept"] += 1
                src_stat["kept"] += 1

        with conn:
            store.save_source_health(conn, company["name"], company.get("ats"),
                                     src_stat, now)

    # Record this run in history. trashed is always 0 now (no scoring at fetch).
    with conn:
        conn.execute(
            "INSERT INTO runs (kind, fetched, seen, dropped, trashed, kept, errors) "
            "VALUES ('fetch', ?, ?, ?, 0, ?, ?)",
            (stats["fetched"], stats["seen"], stats["dropped"],
             stats["kept"], stats["errors"]),
        )
    conn.close()

    # Telegram summary (a no-op if not configured or disabled). No scoring, so no
    # model name and no ranked new-jobs list.
    notify.send(notify.run_summary(stats, time.time() - start_ts, "", []))

    print("\n=== Run summary ===")
    for k, v in stats.items():
        print(f"  {k:12} {v}")
    print(f"  {'elapsed':12} {time.time() - start_ts:.1f}s")


if __name__ == "__main__":
    run()
