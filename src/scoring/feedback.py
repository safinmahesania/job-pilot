"""Your own decisions, fed back into the scoring.

The model's opinion of a job is a guess about what you want. Your saved and
dismissed jobs are the answer. Every time you dismiss something the model scored
85, that is a labelled error sitting in the database — and it costs nothing to
show it to the model next time.

So the scoring prompt carries a short calibration block: a handful of jobs you
kept, a handful you threw away, and — most usefully — the ones the model rated
highly that you rejected anyway. Those are where its model of you is wrong.

Two deliberate limits:

  * Compact. Title, company and (for mistakes) the score it gave. Not the whole
    posting. One provider in the chain caps out at an 8K context, and a bloated
    prompt would push the actual job description out of it.

  * Silent below a threshold. With two dismissals there is no pattern to learn,
    only noise to overfit to. Below FEEDBACK_MIN_EXAMPLES the block is omitted
    entirely and scoring behaves exactly as it did before.
"""
from src.paths import (
    FEEDBACK_SAVED_EXAMPLES,
    FEEDBACK_DISMISSED_EXAMPLES,
    FEEDBACK_MIN_EXAMPLES,
)


def _rows(conn, user_id, status: str, limit: int) -> list[dict]:
    rows = conn.execute(
        "SELECT j.title, j.company, j.location, uj.score "
        "FROM jobs j JOIN user_jobs uj ON uj.job_id = j.id "
        "WHERE uj.user_id = ? AND uj.status = ? AND j.title IS NOT NULL "
        "ORDER BY j.id DESC LIMIT ?",
        (user_id, status, limit),
    ).fetchall()
    return [{"title": r[0], "company": r[1], "location": r[2], "score": r[3]}
            for r in rows]


def examples(conn, user_id) -> str:
    """A calibration block for the scoring prompt, or "" when there isn't one.

    Returns text to be pasted into the prompt. Empty string means: not enough
    decisions yet — score exactly as before.
    """
    saved = _rows(conn, user_id, "saved", FEEDBACK_SAVED_EXAMPLES)
    # Applied is a stronger signal than saved — someone bothered to apply.
    applied = _rows(conn, user_id, "applied", FEEDBACK_SAVED_EXAMPLES)
    dismissed = _rows(conn, user_id, "dismissed", FEEDBACK_DISMISSED_EXAMPLES)

    wanted = applied + [s for s in saved
                        if (s["title"], s["company"]) not in
                        {(a["title"], a["company"]) for a in applied}]
    wanted = wanted[:FEEDBACK_SAVED_EXAMPLES]

    if len(wanted) + len(dismissed) < FEEDBACK_MIN_EXAMPLES:
        return ""

    def line(job: dict) -> str:
        where = f" — {job['location']}" if job.get("location") else ""
        return f"- {job['title']} at {job['company']}{where}"

    parts = ["THE CANDIDATE'S OWN PAST DECISIONS — calibrate against these.",
             "They are the ground truth about what this person actually wants; "
             "your scores should agree with them."]

    if wanted:
        parts.append("\nJobs they KEPT (saved or applied to):")
        parts += [line(j) for j in wanted]

    if dismissed:
        parts.append("\nJobs they DISMISSED:")
        parts += [line(j) for j in dismissed]

    # The most informative rows: the model said yes, the human said no.
    mistakes = [j for j in dismissed if (j.get("score") or 0) >= 70]
    if mistakes:
        parts.append(
            "\nWhere the scoring was WRONG — these were scored highly and the "
            "candidate dismissed them anyway. Work out what they have in common "
            "and score that kind of job lower:"
        )
        parts += [f"- {j['title']} at {j['company']} — was scored {int(j['score'])}"
                  for j in mistakes]

    parts.append(
        "\nDo not copy these scores mechanically. Use them to understand the "
        "candidate's taste — which roles, levels, domains and locations they "
        "actually pursue — and apply that understanding to the job below."
    )
    return "\n".join(parts)


def stats(conn, user_id) -> dict:
    """What the feedback loop currently has to work with — shown in Settings."""
    def count(where, *args):
        return conn.execute(
            "SELECT COUNT(*) FROM user_jobs uj WHERE uj.user_id = ? " + where,
            (user_id, *args)).fetchone()[0]

    saved = count("AND uj.status='saved'")
    applied = count("AND uj.status='applied'")
    dismissed = count("AND uj.status='dismissed'")
    mistakes = count("AND uj.status='dismissed' AND uj.score >= 70")
    total = saved + applied + dismissed

    return {
        "saved": saved,
        "applied": applied,
        "dismissed": dismissed,
        "high_scored_but_dismissed": mistakes,
        "active": total >= FEEDBACK_MIN_EXAMPLES,
        "needed": max(0, FEEDBACK_MIN_EXAMPLES - total),
    }
