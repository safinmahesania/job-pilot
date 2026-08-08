"""Homepage dashboards — one call each for the user and admin landing pages.

The homepages pull from a lot of places (job counts, the funnel, follow-ups, recent
activity, run history, source health, AI quota). Rather than have the frontend fan out
a dozen requests on load, each dashboard is assembled here and returned whole.
"""
from fastapi import APIRouter, Depends

from src.deps import _db_dep, _get_setting

router = APIRouter()


def _q(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


@router.get("/api/dashboard/user")
def user_dashboard(conn=Depends(_db_dep)):
    """Everything the user's home page shows: a greeting line's numbers, today's top
    matches, the pipeline funnel, follow-ups due, and a short recent-activity feed."""
    threshold = int(_get_setting(conn, "score_threshold", 70))

    # Headline numbers.
    new_today = _q(
        conn,
        "SELECT COUNT(*) FROM jobs WHERE status='surfaced' AND date(fetched_at)=date('now')",
    )
    saved = _q(conn, "SELECT COUNT(*) FROM jobs WHERE status='saved'")
    applied = _q(conn, "SELECT COUNT(*) FROM jobs WHERE status='applied'")
    avg_score = _q(conn, "SELECT ROUND(AVG(score)) FROM jobs WHERE score IS NOT NULL") or 0

    # Review progress this week: of jobs surfaced in the last 7 days, how many have been
    # acted on (moved out of 'surfaced').
    surfaced_wk = _q(
        conn,
        "SELECT COUNT(*) FROM jobs WHERE fetched_at >= date('now','-6 days')",
    )
    reviewed_wk = _q(
        conn,
        "SELECT COUNT(*) FROM jobs WHERE fetched_at >= date('now','-6 days') "
        "AND status != 'surfaced'",
    )
    reviewed_pct = round((reviewed_wk / surfaced_wk) * 100) if surfaced_wk else 0

    # Top matches: highest-scoring jobs still in the feed.
    rows = conn.execute(
        "SELECT id, title, company, location, score, fetched_at FROM jobs "
        "WHERE status='surfaced' AND score IS NOT NULL AND score >= ? "
        "ORDER BY score DESC, fetched_at DESC LIMIT 5",
        (threshold,),
    ).fetchall()
    top_matches = [
        {"id": r[0], "title": r[1], "company": r[2], "location": r[3],
         "score": r[4], "fetched_at": r[5]}
        for r in rows
    ]

    # Pipeline funnel.
    funnel = {
        s: _q(conn, "SELECT COUNT(*) FROM jobs WHERE status=?", s)
        for s in ("surfaced", "saved", "applied", "interview", "offer")
    }

    # Follow-ups due.
    from src import followups
    fu_items = followups.due(conn)
    fu_summary = followups.summary(conn)

    # Recent activity: recently actioned jobs, newest first. Uses the timestamps the
    # app already records (applied date, else fetch date).
    activity = []
    act_rows = conn.execute(
        "SELECT title, company, status, "
        "COALESCE(applied_on, fetched_at) AS ts FROM jobs "
        "WHERE status IN ('saved','applied','interview','offer') "
        "ORDER BY ts DESC LIMIT 5"
    ).fetchall()
    for r in act_rows:
        activity.append({"title": r[0], "company": r[1], "status": r[2], "at": r[3]})

    return {
        "new_today": new_today,
        "saved": saved,
        "applied": applied,
        "avg_score": avg_score,
        "reviewed_pct": reviewed_pct,
        "reviewed_wk": reviewed_wk,
        "surfaced_wk": surfaced_wk,
        "top_matches": top_matches,
        "funnel": funnel,
        "followups": fu_items[:4],
        "followups_total": fu_summary.get("total", 0),
        "activity": activity,
    }


@router.get("/api/dashboard/admin")
def admin_dashboard(conn=Depends(_db_dep)):
    """Everything the admin cockpit shows: system pulse, the latest run, AI quota,
    source health, and the kept-per-run sparkline."""
    from src import store

    threshold = int(_get_setting(conn, "score_threshold", 70))

    total = _q(conn, "SELECT COUNT(*) FROM jobs")
    feed = _q(
        conn,
        "SELECT COUNT(*) FROM jobs WHERE status='surfaced' AND score >= ?",
        threshold,
    )
    new_today = _q(
        conn,
        "SELECT COUNT(*) FROM jobs WHERE date(fetched_at)=date('now')",
    )
    avg_score = _q(conn, "SELECT ROUND(AVG(score)) FROM jobs WHERE score IS NOT NULL") or 0

    # Recent runs → latest run summary + kept sparkline (oldest→newest, last 12).
    runs = store.recent_runs(conn, limit=12)
    latest = runs[0] if runs else None
    spark = [
        {"kept": r["kept"] or 0, "at": r["at"], "fetched": r["fetched"] or 0,
         "errors": r["errors"] or 0}
        for r in reversed(runs)
    ]
    kept_total = sum(s["kept"] for s in spark)
    kept_best = max((s["kept"] for s in spark), default=0)
    kept_avg = round(kept_total / len(spark)) if spark else 0

    # Source health verdicts.
    from src import health
    try:
        boards = health.assess(conn)
    except Exception:
        boards = []
    broken = [b for b in boards if b.get("verdict") in ("silent", "erroring", "never_worked")]

    # AI provider quota.
    from src.routes.providers import llm_providers
    try:
        prov = llm_providers()
        providers = prov.get("providers", [])
    except Exception:
        providers = []

    # Scheduler timing, if available.
    last_fetch = latest["at"] if latest else None

    return {
        "total": total,
        "feed": feed,
        "new_today": new_today,
        "avg_score": avg_score,
        "provider_count": len([p for p in providers if p.get("available")]),
        "latest_run": latest,
        "last_fetch": last_fetch,
        "kept_spark": spark,
        "kept_total": kept_total,
        "kept_best": kept_best,
        "kept_avg": kept_avg,
        "boards": boards,
        "broken": broken,
        "providers": providers,
    }
