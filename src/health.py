"""Which boards have stopped working, and whether you would ever have noticed.

A scraper does not usually fail loudly. It fails by returning HTTP 200 and an
empty list, because the company moved to a different ATS, or the API grew a
required parameter, or the selector changed one word. The run succeeds. The Health
tab stays green. Jobs from that source simply stop arriving, and nothing tells
you — you just quietly get fewer results forever.

That is what this module exists to catch. It distinguishes three kinds of trouble:

  * **erroring** — the fetch threw. Loud, obvious, already visible.
  * **silent** — HTTP 200, zero jobs, several runs running. This is the one that
    matters, and the one nothing else in the app would ever tell you about.
  * **never worked** — it has never returned a single job since it was added.
    Usually a bad slug, or an adapter that is a stub. (The `ats: custom` entries
    land here, which is exactly right: they fetch nothing, and now they say so.)

A board that recovers resets its own streak and its alert flag, so a source that
breaks, gets fixed, and breaks again will tell you both times.
"""
from src.paths import HEALTH_ZERO_STREAK, HEALTH_ERROR_STREAK


def assess(conn) -> list[dict]:
    """Every board, with a verdict attached."""
    rows = conn.execute(
        "SELECT name, ats, fetched, kept, status, error, last_run, "
        "zero_streak, error_streak, last_ok, alerted FROM source_health "
        "ORDER BY name"
    ).fetchall()

    out = []
    for (name, ats, fetched, kept, status, error, last_run,
         zero_streak, error_streak, last_ok, alerted) in rows:

        if error_streak >= HEALTH_ERROR_STREAK:
            verdict = "erroring"
            detail = (f"Failed {error_streak} runs in a row. "
                      f"{(error or '').strip()[:120]}")
        elif zero_streak >= HEALTH_ZERO_STREAK and not last_ok:
            verdict = "never_worked"
            detail = (f"Has never returned a job in {zero_streak} runs. "
                      f"Check the slug, or the adapter may be a stub.")
        elif zero_streak >= HEALTH_ZERO_STREAK:
            verdict = "silent"
            detail = (f"Returned nothing for {zero_streak} runs — it last worked "
                      f"on {str(last_ok)[:10]}. It reports success, so nothing "
                      f"else would have told you.")
        elif zero_streak or error_streak:
            verdict = "wobbling"
            detail = (f"{zero_streak or error_streak} bad run(s) so far. "
                      f"Not conclusive yet.")
        else:
            verdict = "ok"
            detail = f"{fetched} fetched, {kept} kept."

        out.append({
            "name": name, "ats": ats, "fetched": fetched, "kept": kept,
            "status": status, "error": error, "last_run": last_run,
            "last_ok": last_ok, "zero_streak": zero_streak,
            "error_streak": error_streak, "alerted": bool(alerted),
            "verdict": verdict, "detail": detail,
        })
    return out


BROKEN = {"erroring", "silent", "never_worked"}


def broken(conn) -> list[dict]:
    """Boards that are properly broken, not merely having an off day."""
    return [b for b in assess(conn) if b["verdict"] in BROKEN]


def new_breakages(conn) -> list[dict]:
    """Broken boards you have not been told about yet.

    The alert flag is what keeps this from becoming noise. A board that has been
    dead for a month is not news every four hours; it was news once.
    """
    return [b for b in broken(conn) if not b["alerted"]]


def summary(conn) -> dict:
    boards = assess(conn)
    return {
        "total": len(boards),
        "ok": sum(1 for b in boards if b["verdict"] == "ok"),
        "wobbling": sum(1 for b in boards if b["verdict"] == "wobbling"),
        "silent": sum(1 for b in boards if b["verdict"] == "silent"),
        "erroring": sum(1 for b in boards if b["verdict"] == "erroring"),
        "never_worked": sum(1 for b in boards if b["verdict"] == "never_worked"),
        "broken": sum(1 for b in boards if b["verdict"] in BROKEN),
    }


def week_stats(conn) -> dict:
    """Everything the weekly digest needs, in one place."""
    from src import followups

    def one(sql, *args):
        return conn.execute(sql, args).fetchone()[0]

    week = "-7 days"
    return {
        "new_jobs": one(
            "SELECT COUNT(*) FROM jobs WHERE fetched_at >= datetime('now', ?)",
            week),
        "applied": one(
            "SELECT COUNT(*) FROM jobs WHERE status='applied' "
            "AND applied_on >= date('now', ?)", week),
        "saved": one("SELECT COUNT(*) FROM jobs WHERE status='saved'"),
        "dismissed": one("SELECT COUNT(*) FROM jobs WHERE status='dismissed'"),
        "runs": one(
            "SELECT COUNT(*) FROM runs WHERE started_at >= datetime('now', ?)",
            week),
        "unreviewed": one(
            "SELECT COUNT(*) FROM jobs WHERE status='surfaced' AND score IS NOT NULL"),
        "followups_due": followups.summary(conn)["total"],
        "broken_boards": summary(conn)["broken"],
    }
