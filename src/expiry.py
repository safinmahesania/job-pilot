"""Has a job's application window closed?

A posting that stopped accepting applications last week is noise: it sits in the feed
looking live, and every minute spent reading it is wasted. Two things say a job has
closed — a `deadline` the adapter captured, and a date written into the description
itself ("apply by March 15", "closing date: 2025-03-15"). Either one in the past means
the door is shut.

The date in the text is read conservatively. A wrong guess here dismisses a job that is
still open, which is worse than missing one that has closed — so only clearly
deadline-shaped phrases count, and anything ambiguous is left alone.
"""
from __future__ import annotations

import re
from datetime import date, datetime

#: Phrases that introduce a real deadline, followed by a date. Not every date in a
#: posting is a deadline — "posted on", "founded in 2019", a salary of "$120,000" — so a
#: bare date is ignored; only one behind a closing-date cue is trusted.
_CUE = (r"(?:appl(?:y|ication)|closing|closes?|deadline|last date|apply before|"
        r"apply by|submissions? (?:close|due)|no later than)")

_MONTHS = ("january february march april may june july august september october "
           "november december").split()
_MONTH_NUM = {m[:3]: i + 1 for i, m in enumerate(_MONTHS)}

# 2025-03-15  /  15 March 2025  /  March 15, 2025  /  15/03/2025
_ISO = re.compile(rf"{_CUE}\D{{0,20}}?(\d{{4}})-(\d{{2}})-(\d{{2}})", re.I)
_DMY_TEXT = re.compile(
    rf"{_CUE}[^0-9a-z]{{0,20}}?(\d{{1,2}})\s+([a-z]+)\s+(\d{{4}})", re.I)
_MDY_TEXT = re.compile(
    rf"{_CUE}[^0-9a-z]{{0,20}}?\b([a-z]+)\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", re.I)
_DMY_SLASH = re.compile(
    rf"{_CUE}\D{{0,20}}?(\d{{1,2}})/(\d{{1,2}})/(\d{{4}})", re.I)


def _try(fn):
    try:
        return fn()
    except (ValueError, KeyError, TypeError):
        return None


def parse_deadline(text: str | None) -> date | None:
    """The application deadline written in the text, or None if none is clearly stated."""
    if not text:
        return None
    t = text[:4000]                       # deadlines live near the top; don't scan forever

    m = _ISO.search(t)
    if m:
        d = _try(lambda: date(int(m[1]), int(m[2]), int(m[3])))
        if d:
            return d

    m = _DMY_TEXT.search(t)
    if m:
        mon = _MONTH_NUM.get(m[2][:3].lower())
        d = _try(lambda: date(int(m[3]), mon, int(m[1]))) if mon else None
        if d:
            return d

    m = _MDY_TEXT.search(t)
    if m:
        mon = _MONTH_NUM.get(m[1][:3].lower())
        d = _try(lambda: date(int(m[3]), mon, int(m[2]))) if mon else None
        if d:
            return d

    m = _DMY_SLASH.search(t)
    if m:
        # Ambiguous d/m vs m/d. Only trust it when the first number cannot be a month,
        # so a genuine day-first date reads correctly and an American m/d/y is left for
        # the text forms above rather than silently mis-parsed.
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        if a > 12 and b <= 12:
            return _try(lambda: date(y, b, a))

    return None


def _as_date(value) -> date | None:
    """A stored deadline field into a date. Adapters store these as ISO strings, mostly."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()[:10]
    return _try(lambda: datetime.strptime(s, "%Y-%m-%d").date())


def deadline_for(job: dict) -> date | None:
    """The best deadline known for a job: the stored field first, then the description.

    The stored field is the adapter's own reading and is preferred; the description is
    the fallback for the many sources that never fill the field.
    """
    return _as_date(job.get("deadline")) or parse_deadline(job.get("description"))


def has_expired(job: dict, *, today: date | None = None) -> bool:
    """Whether the application window has closed, as of today."""
    d = deadline_for(job)
    if d is None:
        return False                      # no known deadline is not an expired one
    return d < (today or date.today())
