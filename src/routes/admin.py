"""Maintenance, error log, run history, and manual pipeline triggers.

The housekeeping surface: rescore everything against the current profile, clear jobs
below the threshold or older than N days, export to CSV, reload config, empty caches,
and the two destructive resets. Plus the read-only views the admin panel shows — the
error log and the fetch-run history — and the button that kicks off a fetch by hand.
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from src import configio, maintenance, scheduler, store
from src.deps import _db_dep, _get_setting

router = APIRouter()


@router.get("/api/setup-status")
def setup_status(conn=Depends(_db_dep)):
    """Tell the UI how far along setup is, so a new user gets a checklist instead of a
    blank feed. Each step is a plain boolean plus a short label the frontend renders."""
    profile = configio.read_yaml("profile.yaml") or {}
    companies = (configio.read_yaml("companies.yaml") or {}).get("companies", [])
    active_sources = [c for c in companies if c.get("active")]

    # A profile is "set up" once it has any of the fields that actually drive scoring —
    # not just an empty file with headers.
    prof_search = profile.get("search", {}) or {}
    prof_constraints = profile.get("constraints", {}) or {}
    has_profile = bool(prof_search or prof_constraints)

    def _count(sql):
        try:
            return conn.execute(sql).fetchone()[0]
        except Exception:                              # table not created yet
            return 0

    runs = _count("SELECT COUNT(*) FROM runs")
    total_jobs = _count("SELECT COUNT(*) FROM jobs")

    steps = [
        {"key": "profile", "done": has_profile,
         "label": "Set up your profile",
         "hint": "Tell JobPilot what roles, locations and level you want."},
        {"key": "sources", "done": len(active_sources) > 0,
         "label": "Add job sources",
         "hint": "Pick the company boards to fetch jobs from."},
        {"key": "run", "done": runs > 0,
         "label": "Run your first fetch",
         "hint": "Pull jobs from your sources and score them against your profile."},
        {"key": "review", "done": total_jobs > 0,
         "label": "Review your matches",
         "hint": "See scored jobs in your feed and start applying."},
    ]
    done = sum(1 for s in steps if s["done"])
    return {
        "steps": steps,
        "completed": done,
        "total": len(steps),
        "all_done": done == len(steps),
        "active_sources": len(active_sources),
    }


# ── Maintenance ──

@router.post("/api/maint/rescore")
def maint_rescore():
    return maintenance.rescore_all()


class ScoreRequest(BaseModel):
    job_ids: list[int]


@router.get("/api/notifications")
def list_notifications(conn=Depends(_db_dep)):
    """Recent notifications for the bell in the UI, newest first, with an unseen count."""
    rows = conn.execute(
        "SELECT id, text, created_at, seen FROM notifications "
        "ORDER BY id DESC LIMIT 50"
    ).fetchall()
    items = [{"id": r[0], "text": r[1], "created_at": r[2], "seen": bool(r[3])}
             for r in rows]
    unseen = sum(1 for it in items if not it["seen"])
    return {"notifications": items, "unseen": unseen}


@router.post("/api/notifications/seen")
def mark_notifications_seen(conn=Depends(_db_dep)):
    """Mark everything as seen — called when the user opens the notifications panel."""
    with conn:
        conn.execute("UPDATE notifications SET seen = 1 WHERE seen = 0")
    return {"ok": True}


@router.get("/api/jobs/enrich-diagnosis")
def enrich_diagnosis(conn=Depends(_db_dep)):
    """Where do the feed's Adzuna links actually point? No fetching — just a tally.

    "Fetch full descriptions" can come back having enriched almost nothing, and the
    reason is usually that the links don't go anywhere fetchable: an Adzuna job whose
    redirect lands on Indeed or LinkedIn is left on its snippet by design. This counts
    the short Adzuna jobs by destination so that's visible at a glance, and lists a few
    example hosts that fell outside the allowlist — which is the real answer to "why are
    descriptions still short".
    """
    from urllib.parse import urlparse

    from src import enrich

    rows = conn.execute(
        "SELECT source_url, apply_url, description FROM jobs "
        "WHERE source='adzuna' AND status='surfaced' "
        "AND (description IS NULL OR length(description) < 400 "
        "     OR trim(description) LIKE '%…' OR trim(description) LIKE '%...')"
    ).fetchall()

    by_strategy = {"adzuna": 0, "lever": 0, "greenhouse": 0, "not_fetchable": 0}
    other_hosts: dict[str, int] = {}
    for source_url, apply_url, _desc in rows:
        url = source_url or apply_url or ""
        strat = enrich._destination(url)
        if strat:
            by_strategy[strat] += 1
        else:
            by_strategy["not_fetchable"] += 1
            host = (urlparse(url).hostname or "unknown").replace("www.", "")
            other_hosts[host] = other_hosts.get(host, 0) + 1

    # The handful of hosts most of the un-fetchable links go to.
    top_other = sorted(other_hosts.items(), key=lambda kv: kv[1], reverse=True)[:8]

    fetchable = {k: v for k, v in by_strategy.items() if k != "not_fetchable"}

    # Also push it to Telegram, so the breakdown can be read on a phone rather than only
    # in a browser alert — the same numbers, formatted for a message.
    from src import notify
    hosts_line = ", ".join(f"{h} ({n})" for h, n in top_other) or "none"
    notify.send(
        "<b>Adzuna enrichment — diagnosis</b>\n"
        f"Short jobs: {len(rows)}\n"
        f"Fetchable → Adzuna {fetchable.get('adzuna', 0)}, "
        f"Lever {fetchable.get('lever', 0)}, "
        f"Greenhouse {fetchable.get('greenhouse', 0)}\n"
        f"Not fetchable: {by_strategy['not_fetchable']}\n"
        f"Unfetchable hosts: {hosts_line}"
    )

    return {
        "short_adzuna_jobs": len(rows),
        "fetchable": fetchable,
        "not_fetchable": by_strategy["not_fetchable"],
        "top_unfetchable_hosts": [{"host": h, "count": n} for h, n in top_other],
    }


@router.post("/api/jobs/enrich-existing")
def enrich_existing(conn=Depends(_db_dep)):
    """Fetch full descriptions for Adzuna jobs already in the feed that only have a snippet.

    Enrichment runs during a fetch, so jobs saved before it existed — or before their
    link pointed somewhere fetchable — still carry Adzuna's truncated snippet. This walks
    the live Adzuna jobs whose description is short, fetches the full posting for the ones
    whose link is fetchable (Adzuna / Lever / Greenhouse), saves it, and re-scores them so
    the new score reflects the whole posting rather than the teaser.

    It only touches short Adzuna jobs, so running it twice is cheap — the second run finds
    nothing left to do.
    """
    from src import enrich
    from src.paths import MIN_DESCRIPTION_CHARS
    from src.routes.jobs import _rescore_one
    from src.scoring.rerank import build_calibration

    scoring_on = _get_setting(conn, "scoring_enabled", "1") == "1"

    # A breakdown, because "checked: 2" out of a feed full of Adzuna jobs is confusing
    # without knowing where the rest went. Each number is how many Adzuna jobs fall in
    # that bucket, so the totals explain themselves.
    total_adzuna = conn.execute(
        "SELECT count(*) FROM jobs WHERE source='adzuna'").fetchone()[0]
    surfaced_adzuna = conn.execute(
        "SELECT count(*) FROM jobs WHERE source='adzuna' AND status='surfaced'"
    ).fetchone()[0]
    already_full = conn.execute(
        "SELECT count(*) FROM jobs WHERE source='adzuna' AND status='surfaced' "
        "AND description IS NOT NULL AND length(description) >= 400 "
        "AND trim(description) NOT LIKE '%…' AND trim(description) NOT LIKE '%...'"
    ).fetchone()[0]

    rows = conn.execute(
        "SELECT id, source, source_url, apply_url, description "
        "FROM jobs WHERE source='adzuna' AND status='surfaced' "
        # Short, OR ends in an ellipsis — Adzuna truncates its snippet and leaves a "…"
        # (or "...") behind, so a description can be over 400 characters and still be cut
        # off mid-sentence. Length alone misses those; the trailing marker catches them.
        "AND (description IS NULL OR length(description) < 400 "
        "     OR trim(description) LIKE '%…' OR trim(description) LIKE '%...')"
    ).fetchall()

    calibration = build_calibration() if scoring_on else ""
    enriched = 0
    rescored = 0
    not_fetchable = 0        # short, but the link isn't Adzuna/Lever/Greenhouse
    fetch_failed = 0         # fetchable, but the page gave nothing usable
    for row in rows[:150]:                  # cap one request; run again for the rest
        job = {"source": row[1], "source_url": row[2], "apply_url": row[3],
               "description": row[4]}
        if not enrich.is_enrichable(job):
            not_fetchable += 1
            continue
        full = enrich.full_description(job)
        if not full or len(full) < MIN_DESCRIPTION_CHARS:
            fetch_failed += 1
            continue
        with conn:
            conn.execute("UPDATE jobs SET description=? WHERE id=?", (full, row[0]))
        enriched += 1
        if scoring_on and _rescore_one(conn, row[0], calibration) is not None:
            rescored += 1

    from src import notify
    notify.send(
        "<b>Adzuna enrichment — run</b>\n"
        f"Checked: {len(rows)}\n"
        f"Enriched: {enriched}   Rescored: {rescored}\n"
        f"Already full: {already_full}\n"
        f"Short but not fetchable: {not_fetchable}\n"
        f"Fetch returned nothing: {fetch_failed}\n"
        f"(of {total_adzuna} Adzuna jobs, {surfaced_adzuna} in feed)"
    )

    return {
        "checked": len(rows),
        "enriched": enriched,
        "rescored": rescored,
        # The breakdown that explains the numbers above.
        "adzuna_total": total_adzuna,
        "adzuna_surfaced": surfaced_adzuna,
        "already_full": already_full,
        "short_but_not_fetchable": not_fetchable,
        "fetch_returned_nothing": fetch_failed,
    }


@router.post("/api/jobs/extract-existing")
def extract_existing(conn=Depends(_db_dep)):
    """Backfill the structured fields for jobs that predate extraction.

    Extraction runs during a fetch, so every job saved before this feature existed
    carries a NULL for work_mode, requirements, benefits and the rest. This walks
    the jobs that have a real description but were never extracted, runs one LLM
    pass over each, and writes the fields in place — the score and everything else
    is left untouched.

    Capped per request so one click can't run for an hour on a huge database; the
    counts come back so the UI can tell you to run it again for the rest.
    """
    from src import extract, notify

    # A description too short to extract from would just come back empty — skip it,
    # the same bar the live pipeline uses.
    rows = conn.execute(
        "SELECT id, title, company, location, description "
        "FROM jobs "
        "WHERE extracted_at IS NULL "
        "AND description IS NOT NULL "
        "AND length(trim(description)) >= ?",
        (extract.MIN_DESCRIPTION_CHARS,),
    ).fetchall()

    remaining = len(rows)
    extracted = 0
    skipped = 0        # had a description but the model found nothing to read
    failed = 0         # the call errored — logged, job left NULL to retry later
    for row in rows[:100]:                 # cap one request; run again for the rest
        job = {"title": row[1], "company": row[2], "location": row[3],
               "description": row[4]}
        try:
            ex = extract.extract(job)
        except Exception as e:
            failed += 1
            store.record_error(conn, "extract:backfill", e)
            continue
        if ex is None:
            skipped += 1
            continue
        store.update_extraction(conn, row[0], ex.model_dump())
        extracted += 1

    notify.send(
        "<b>Description extraction — backfill</b>\n"
        f"Extracted: {extracted}\n"
        f"Nothing to read: {skipped}   Failed: {failed}\n"
        f"({remaining} were pending; run again for the rest)"
    )

    return {
        "checked": min(remaining, 100),
        "extracted": extracted,
        "skipped": skipped,
        "failed": failed,
        "remaining": max(0, remaining - 100),
    }


@router.post("/api/jobs/score")
def score_jobs(body: ScoreRequest, conn=Depends(_db_dep)):
    """Score specific jobs on demand — for unscored imports, or to re-run a few.

    Unlike 'rescore everything', this targets only the ids you pass, so you can score
    one job from its card, or a selection, without churning the whole database. Returns
    per-job results so the UI can update just those rows.
    """
    if _get_setting(conn, "scoring_enabled", "1") != "1":
        raise HTTPException(403, "Scoring is off — enable it in Settings first.")

    from src.routes.jobs import _rescore_one
    from src.scoring.rerank import build_calibration

    # Once for the batch, not once per job: building it reads the database, and doing
    # that per job adds a query and a connection to every one of up to 200 jobs.
    calibration = build_calibration()
    results = {}
    for jid in body.job_ids[:200]:          # cap a single request
        results[jid] = _rescore_one(conn, jid, calibration)
    scored = sum(1 for v in results.values() if v is not None)
    return {"requested": len(body.job_ids), "scored": scored, "results": results}


@router.post("/api/maint/cleanup")
def maint_cleanup():
    return maintenance.cleanup_below_threshold()


class DaysBody(BaseModel):
    days: int = 30


@router.post("/api/maint/clear-old")
def maint_clear_old(body: DaysBody):
    return maintenance.clear_old_jobs(body.days)


@router.get("/api/maint/export")
def maint_export():
    csv_data = maintenance.export_csv()
    return Response(content=csv_data, media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=jobpilot_jobs.csv"})


@router.post("/api/maint/reload")
def maint_reload():
    return maintenance.reload_config()


@router.post("/api/maint/restart")
def maint_restart():
    """Restart the server process.

    Config reload re-reads the YAML files but keeps the running code and any stuck
    in-memory state. A full restart re-execs the process, which is what you want after
    pulling new code or when the server is wedged. It replies first, then restarts a
    moment later so this request completes cleanly (the browser then reconnects on its
    own). If the server is run under a supervisor that respawns it, or with --reload,
    this comes back on its own; a bare `uvicorn` call will not, so run it from the
    provided loop script.
    """
    return maintenance.schedule_restart()


@router.post("/api/maint/clean-cache")
def maint_clean_cache():
    return maintenance.clean_cache()


@router.post("/api/maint/reset")
def maint_reset():
    return maintenance.reset_all_jobs()


@router.post("/api/maint/clear-runs")
def maint_clear_runs():
    return maintenance.clear_run_history()


@router.post("/api/maint/nuclear")
def maint_nuclear():
    return maintenance.nuclear_reset()


@router.get("/api/maint/preview")
def maint_preview(conn=Depends(_db_dep)):
    """Live counts of what each maintenance action would touch.

    The Maintenance tab shows these next to each action — "142 jobs", "18 expired",
    "0 below threshold" — so a run has a visible target and it's clear when there's
    nothing to do. Computed in one request so the tab doesn't fan out a dozen calls.
    """
    from pathlib import Path
    from src import expiry
    from src.paths import MIN_DESCRIPTION_CHARS

    def q(sql, *args):
        return conn.execute(sql, args).fetchone()[0]

    total = q("SELECT COUNT(*) FROM jobs")
    threshold = int(_get_setting(conn, "score_threshold", 70))

    # Feed jobs still carrying a short snippet (candidates for description enrichment).
    snippets = q(
        "SELECT COUNT(*) FROM jobs WHERE status='surfaced' "
        "AND (description IS NULL OR LENGTH(description) < ?)",
        MIN_DESCRIPTION_CHARS,
    )

    # Live jobs past their deadline — the same check sweep-expired uses.
    rows = conn.execute(
        "SELECT deadline, description FROM jobs WHERE status='surfaced'"
    ).fetchall()
    expired = sum(
        1 for r in rows
        if expiry.has_expired({"deadline": r[0], "description": r[1]})
    )

    # Feed jobs scoring under the threshold (cleanup candidates).
    low = q(
        "SELECT COUNT(*) FROM jobs WHERE status='surfaced' AND score IS NOT NULL AND score < ?",
        threshold,
    )

    # Jobs with a real description that were never run through extraction — the
    # backfill target. Uses the same minimum length the extractor itself skips at.
    from src.extract import MIN_DESCRIPTION_CHARS as _MIN_EXTRACT
    unextracted = q(
        "SELECT COUNT(*) FROM jobs WHERE extracted_at IS NULL "
        "AND description IS NOT NULL AND length(trim(description)) >= ?",
        _MIN_EXTRACT,
    )

    # Rough cache size on disk, in MB.
    cache_bytes = 0
    for name in ("__pycache__", "logs", "data/cache"):
        p = Path(name)
        if p.exists():
            cache_bytes += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    cache_mb = round(cache_bytes / (1024 * 1024), 1)

    return {
        "total": total,
        "snippets": snippets,
        "expired": expired,
        "low": low,
        "unextracted": unextracted,
        "cache_mb": cache_mb,
    }


# ── Error log ──

@router.get("/api/errors")
def errors_list(limit: int = 100, conn=Depends(_db_dep)):
    """Everything that has gone wrong, newest first."""
    rows = store.recent_errors(conn, limit)
    return rows


@router.post("/api/errors/clear")
def errors_clear(conn=Depends(_db_dep)):
    n = store.clear_errors(conn)
    return {"cleared": n}


# ── Run history ──

@router.get("/api/runs")
def runs_list(limit: int = 50, conn=Depends(_db_dep)):
    """The fetch history — what each run pulled in and kept."""
    rows = store.recent_runs(conn, limit)
    return rows


# ── Manual pipeline trigger ──

class RunRequest(BaseModel):
    # Optional list of source names to fetch just those (a selective run). Omit for a
    # normal full run over every active source.
    only: list[str] | None = None


@router.post("/api/run")
def trigger_run(body: RunRequest | None = None):
    only = body.only if body else None
    if not scheduler.trigger_async(only=only):
        raise HTTPException(409, "pipeline already running")
    return {"started": True, "selective": bool(only), "sources": only or []}


@router.get("/api/run/status")
def run_status():
    """Whether a run is going, and how far along it is.

    The UI polls this to show a run in progress. "Running" on its own is not much use
    when a pass can take twenty minutes; the counts let it say how far in, and the model
    line says what is doing the work.
    """
    from src.run import PROGRESS
    from src.scoring.rerank import get_model_state

    state = scheduler.get_state()
    state["progress"] = dict(PROGRESS)
    state["model"] = get_model_state()
    return state
