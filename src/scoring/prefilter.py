"""Dynamic hard filter — profile ke constraints se drive hota hai.

Sirf un checks chalte hain jinki key profile['constraints'] me present hai.
Naya rule = profile.yaml me field + yahan ek check function. Bas.
"""
from datetime import datetime, timezone
import re


# Canadian province and territory codes. Job boards write "Mississauga, ON, CA" far more
# often than "Mississauga, Ontario, Canada", and matching only the full words silently
# dropped every such posting — real Ontario jobs thrown away for a formatting choice.
# None of these codes collides with a US state code, so a match is unambiguous evidence
# the posting is Canadian. Matched on word boundaries: plain substring matching would
# find "on" inside "London" and "Toronto".
_CA_PROVINCE_CODES = ("on", "qc", "bc", "ab", "mb", "sk", "ns", "nb", "nl",
                      "pe", "yt", "nt", "nu")
_CA_CODE_RE = re.compile(r"\b(" + "|".join(_CA_PROVINCE_CODES) + r")\b")


def _is_canadian(loc: str) -> bool:
    """Whether a location string names somewhere in Canada, in any common format."""
    if any(w in loc for w in ("canada", "canadian")):
        return True
    return bool(_CA_CODE_RE.search(loc))


# ---------- constraint checks ----------
def _check_locations(job, allowed):
    allowed = [a.lower() for a in (allowed or [])]
    loc = (job.get("location") or "").lower()
    is_global = job.get("scope") == "global"
    if not allowed:
        return True

    # allowed Canadian place ka seedha match -> pass
    if any(a in loc for a in allowed if a != "remote"):
        return True

    # A Canadian posting written in code form — "Mississauga, ON, CA" — is in Canada
    # just as much as one spelled "Mississauga, Ontario". Accept it before the stricter
    # checks below, which only understand the spelled-out words.
    if _is_canadian(loc):
        return True

    canada_words = ("canada", "canadian", "north america", "americas")
    remote_ish = job.get("remote") == 1 or "remote" in loc or loc.strip() in ("", "anywhere", "worldwide", "flexible")

    if is_global:
        # GLOBAL boards: Canada explicitly mention hona chahiye — warna drop
        return any(w in loc for w in canada_words)

    # regional/ATS boards: remote pass jab tak koi foreign place na ho
    if remote_ish:
        foreign = (" us", "usa", "united states", "u.s", ", ny", ", tx", ", wa",
                   "uk", "united kingdom", "europe", "emea", "brazil", "india",
                   "germany", "london", "california", "new york")
        if any(f in loc for f in foreign):
            return False
        cleaned = loc.replace("flexible","").replace("/"," ").replace(","," ")
        for w in ("remote","anywhere","worldwide","global","canada","north america","americas"):
            cleaned = cleaned.replace(w, "")
        for a in allowed:
            cleaned = cleaned.replace(a, "")
        return len([t for t in cleaned.split() if len(t) > 2]) == 0
    return False


def _check_salary_floor(job, floor):
    if not floor or not job.get("salary_max"):
        return True
    return job["salary_max"] >= floor


def _check_sponsorship(job, needs):
    if not needs:
        return True
    text = (job.get("description") or "").lower()
    blockers = ("no visa sponsorship", "unable to sponsor", "not able to sponsor",
                "no sponsorship", "must be authorized to work")
    return not any(b in text for b in blockers)


# ---------- search checks ----------
def _ok_level(job, s):
    title = (job.get("title") or "").lower()
    # senior/lead/mid+ titles drop
    if any(bad.lower() in title for bad in s.get("exclude_levels", [])):
        return False
    return True


def _ok_domain(job, s):
    domains = [d.lower() for d in s.get("domains", [])]
    if not domains:
        return True
    title = (job.get("title") or "").lower()
    return any(d in title for d in domains)     # title only, not description


def _ok_job_type(job, s):
    wanted = [t.lower() for t in s.get("job_types", [])]
    jt = (job.get("job_type") or "").lower()
    if not wanted or not jt or jt == "unknown":
        return True  # type pata nahi -> block mat karo
    return any(w in jt or jt in w for w in wanted)


def _ok_recency(job, s):
    days = s.get("posted_within_days")
    raw = job.get("posted_date")
    if not days or not raw:
        return True
    try:
        d = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days <= days
    except Exception:
        return True  # parse fail -> drop mat karo


def passes(job: dict, profile: dict) -> bool:
    c = profile.get("constraints", {})
    if not _check_locations(job, c.get("locations")):
        return False
    if not _check_salary_floor(job, c.get("salary_floor")):
        return False
    if not _check_sponsorship(job, c.get("needs_sponsorship")):
        return False

    s = profile.get("search", {})
    return (_ok_level(job, s) and _ok_domain(job, s)
            and _ok_job_type(job, s) and _ok_recency(job, s)
            and _ok_exclude_keywords(job, s))


def _ok_exclude_keywords(job, s):
    bad = [k.lower() for k in s.get("exclude_keywords", [])]
    if not bad:
        return True
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    return not any(k in text for k in bad)
