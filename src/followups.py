"""Follow-ups: the applications you sent and then forgot about.

An application that goes unanswered is not usually a rejection — it is an email
sitting unread in a queue. A short, polite nudge after a week is the single
cheapest thing a candidate can do, and it is exactly the thing that gets skipped,
because nothing reminds you.

So: a job you marked applied, that has sat at 'applied' with no follow-up for
FOLLOWUP_FIRST_DAYS, is due one. After you send it, the clock restarts for a
second nudge. Past FOLLOWUP_STALE_DAYS with nothing back, JobPilot stops nagging
and says so plainly — at some point the honest read is that it went nowhere, and
pretending otherwise just keeps a dead application on your list.

Nothing here sends anything for you. It tells you what is due and gets out of the
way; the words are yours to write.
"""
from datetime import date, datetime, timedelta

from src.paths import (
    FOLLOWUP_FIRST_DAYS,
    FOLLOWUP_SECOND_DAYS,
    FOLLOWUP_STALE_DAYS,
)


def _days_since(value: str | None) -> int | None:
    """Whole days between a stored YYYY-MM-DD and today."""
    if not value:
        return None
    try:
        then = datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None
    return (date.today() - then).days


def _snoozed(value: str | None) -> bool:
    if not value:
        return False
    try:
        return date.today() < datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return False


def due(conn) -> list[dict]:
    """Applications that need a nudge today.

    Only status='applied' — once a job reaches interview, offer or rejected, the
    conversation is live or over and a reminder would be noise.
    """
    rows = conn.execute(
        "SELECT id, title, company, apply_url, applied_on, followed_up_on, "
        "followup_snooze, notes FROM jobs "
        "WHERE status = 'applied' AND applied_on IS NOT NULL "
        "ORDER BY applied_on ASC"
    ).fetchall()

    out = []
    for r in rows:
        (job_id, title, company, url, applied_on,
         followed_up, snooze, notes) = r

        if _snoozed(snooze):
            continue

        since_applied = _days_since(applied_on)
        if since_applied is None:
            continue

        since_followup = _days_since(followed_up)

        # Long dead. Say so rather than nagging forever.
        if since_applied >= FOLLOWUP_STALE_DAYS and (
            since_followup is None or since_followup >= FOLLOWUP_SECOND_DAYS
        ):
            reason, stage = (
                f"Applied {since_applied} days ago, no reply after "
                f"{'a follow-up' if followed_up else 'no follow-up'}. "
                f"This one has probably gone nowhere — close it out.",
                "stale",
            )

        # Followed up already; time for one more.
        elif followed_up:
            if since_followup < FOLLOWUP_SECOND_DAYS:
                continue
            reason, stage = (
                f"Followed up {since_followup} days ago and heard nothing. "
                f"One more nudge, then let it go.",
                "second",
            )

        # Never followed up, and the first window has passed.
        elif since_applied >= FOLLOWUP_FIRST_DAYS:
            reason, stage = (
                f"Applied {since_applied} days ago, never followed up.",
                "first",
            )
        else:
            continue

        out.append({
            "id": job_id,
            "title": title,
            "company": company,
            "apply_url": url,
            "applied_on": applied_on,
            "followed_up_on": followed_up,
            "days_since_applied": since_applied,
            "days_since_followup": since_followup,
            "stage": stage,              # first | second | stale
            "reason": reason,
            "notes": notes,
        })

    # Most overdue first — that's the order you should work through them.
    out.sort(key=lambda j: -j["days_since_applied"])
    return out


def mark_followed_up(conn, job_id: int, when: str | None = None) -> bool:
    """You sent the nudge. Restart the clock."""
    stamp = when or date.today().isoformat()
    cur = conn.execute(
        "UPDATE jobs SET followed_up_on = ?, followup_snooze = NULL "
        "WHERE id = ? AND status = 'applied'",
        (stamp, job_id),
    )
    conn.commit()
    return cur.rowcount > 0


def snooze(conn, job_id: int, days: int = 7) -> bool:
    """Not now. Don't mention it again until `days` from today."""
    until = (date.today() + timedelta(days=max(1, days))).isoformat()
    cur = conn.execute(
        "UPDATE jobs SET followup_snooze = ? WHERE id = ?",
        (until, job_id),
    )
    conn.commit()
    return cur.rowcount > 0


def summary(conn) -> dict:
    """Counts for the badge in the UI."""
    items = due(conn)
    return {
        "total": len(items),
        "first": sum(1 for i in items if i["stage"] == "first"),
        "second": sum(1 for i in items if i["stage"] == "second"),
        "stale": sum(1 for i in items if i["stage"] == "stale"),
    }


def notification(conn) -> str | None:
    """A Telegram message, or None when nothing is due.

    Deliberately terse. A reminder that reads like a report gets ignored.
    """
    items = due(conn)
    if not items:
        return None

    lines = [f"📮 {len(items)} follow-up{'s' if len(items) != 1 else ''} due"]
    for job in items[:8]:
        mark = {"first": "•", "second": "••", "stale": "✕"}[job["stage"]]
        lines.append(f"{mark} {job['title']} — {job['company']} "
                     f"({job['days_since_applied']}d)")
    if len(items) > 8:
        lines.append(f"…and {len(items) - 8} more")
    return "\n".join(lines)
