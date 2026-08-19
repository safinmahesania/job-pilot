"""Homepage dashboards — the user's personal landing page and the admin cockpit.

Each is assembled server-side and returned whole so the frontend makes one call
rather than fanning out a dozen. The user dashboard is scoped to the caller's own
feed (user_jobs); the admin dashboard is system-wide and admin-only.
"""
from fastapi import APIRouter, Depends

from src.auth import current_user_id
from src.deps import _db_dep, _user_threshold, require_admin

router = APIRouter()


@router.get("/api/dashboard/user")
def user_dashboard(user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    """Everything the user's home page shows: headline numbers, today's top matches,
    the pipeline funnel, follow-ups due, and a short recent-activity feed — all over
    the jobs served to THIS user."""
    threshold = _user_threshold(conn, user_id)
    base = "FROM jobs j JOIN user_jobs uj ON uj.job_id = j.id WHERE uj.user_id = ?"

    def scalar(select, extra="", *a):
        return conn.execute(f"SELECT {select} {base} {extra}",
                            (user_id, *a)).fetchone()[0]

    new_today = scalar("COUNT(*)",
                       "AND uj.status='surfaced' AND uj.served_at::date = CURRENT_DATE")
    saved = scalar("COUNT(*)", "AND uj.status='saved'")
    applied = scalar("COUNT(*)", "AND uj.status='applied'")
    avg_score = scalar("ROUND(AVG(uj.score))", "AND uj.score IS NOT NULL") or 0

    # Review progress this week: of jobs served in the last 7 days, how many acted on.
    surfaced_wk = scalar("COUNT(*)", "AND uj.served_at >= (CURRENT_DATE - 6)")
    reviewed_wk = scalar("COUNT(*)",
                         "AND uj.served_at >= (CURRENT_DATE - 6) AND uj.status != 'surfaced'")
    reviewed_pct = round((reviewed_wk / surfaced_wk) * 100) if surfaced_wk else 0

    # Top matches: highest-scoring jobs still in the feed.
    rows = conn.execute(
        f"SELECT j.id, j.title, j.company, j.location, uj.score, j.fetched_at {base} "
        "AND uj.status='surfaced' AND uj.score IS NOT NULL AND uj.score >= ? "
        "ORDER BY uj.score DESC, j.fetched_at DESC LIMIT 5", (user_id, threshold)
    ).fetchall()
    top_matches = [
        {"id": r[0], "title": r[1], "company": r[2], "location": r[3],
         "score": r[4], "fetched_at": r[5]}
        for r in rows
    ]

    funnel = {
        s: scalar("COUNT(*)", "AND uj.status=?", s)
        for s in ("surfaced", "saved", "applied", "interview", "offer")
    }

    # follow-ups: followups.py still reads the old single-user shape; report empty
    # until it's ported to user_jobs (avoids poisoning the transaction).
    fu_items: list = []
    fu_total = 0

    # Recent activity: recently actioned jobs, newest first.
    activity = []
    act_rows = conn.execute(
        f"SELECT j.title, j.company, uj.status, "
        f"COALESCE(uj.applied_on, uj.served_at) AS ts {base} "
        "AND uj.status IN ('saved','applied','interview','offer') "
        "ORDER BY ts DESC LIMIT 5", (user_id,)
    ).fetchall()
    for r in act_rows:
        activity.append({"title": r[0], "company": r[1], "status": r[2], "at": r[3]})

    return {
        "new_today": new_today, "saved": saved, "applied": applied,
        "avg_score": avg_score, "reviewed_pct": reviewed_pct,
        "reviewed_wk": reviewed_wk, "surfaced_wk": surfaced_wk,
        "top_matches": top_matches, "funnel": funnel,
        "followups": fu_items[:4], "followups_total": fu_total,
        "activity": activity,
    }


@router.get("/api/dashboard/admin")
def admin_dashboard(admin_id: str = Depends(require_admin), conn=Depends(_db_dep)):
    """The admin cockpit: system-wide pulse, the latest run, AI quota, source health,
    and the kept-per-run sparkline. Admin-only."""
    from src import store

    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    # System-wide feed activity: surfaced rows across everyone's feeds.
    feed = conn.execute(
        "SELECT COUNT(*) FROM user_jobs WHERE status='surfaced'").fetchone()[0]
    new_today = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE fetched_at::date = CURRENT_DATE"
    ).fetchone()[0]
    avg_score = conn.execute(
        "SELECT ROUND(AVG(score)) FROM user_jobs WHERE score IS NOT NULL"
    ).fetchone()[0] or 0

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

    from src import health
    try:
        boards = health.assess(conn)
    except Exception:
        boards = []
    broken = [b for b in boards
              if b.get("verdict") in ("silent", "erroring", "never_worked")]

    from src.routes.providers import llm_providers
    try:
        providers = llm_providers().get("providers", [])
    except Exception:
        providers = []

    last_fetch = latest["at"] if latest else None

    return {
        "total": total, "feed": feed, "new_today": new_today, "avg_score": avg_score,
        "provider_count": len([p for p in providers if p.get("available")]),
        "latest_run": latest, "last_fetch": last_fetch,
        "kept_spark": spark, "kept_total": kept_total, "kept_best": kept_best,
        "kept_avg": kept_avg, "boards": boards, "broken": broken,
        "providers": providers,
    }
