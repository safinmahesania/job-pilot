"""Dynamic hard filter — profile ke constraints se drive hota hai.

Sirf un checks chalte hain jinki key profile['constraints'] me present hai.
Naya rule = profile.yaml me field + yahan ek check function. Bas.
"""


def _check_locations(job, allowed, profile):
    allowed = [a.lower() for a in (allowed or [])]
    loc = (job.get("location") or "").lower()
    if not allowed:
        return True
    # allowed jagah ka seedha match?
    if any(a in loc for a in allowed if a != "remote"):
        return True
    # remote hai to sirf tab pass jab koi specific foreign place na ho
    if job.get("remote") == 1 or "remote" in loc:
        cleaned = loc.replace("flexible", "").replace("/", " ").replace(",", " ")
        for w in ("remote", "anywhere", "worldwide", "global"):
            cleaned = cleaned.replace(w, "")
        for a in allowed:
            cleaned = cleaned.replace(a, "")
        leftover = [t for t in cleaned.split() if len(t) > 2]
        return len(leftover) == 0      # koi bacha foreign token = drop
    return False

def _check_salary_floor(job, floor, profile):
    if not floor:
        return True
    if job.get("salary_max") and job["salary_max"] < floor:
        return False
    return True


def _check_seniority(job, level, profile):
    if not level:
        return True
    title = (job.get("title") or "").lower()
    too_senior = ("senior", "staff", "principal", "lead", "director", "head of")
    too_junior = ("intern", "junior", "graduate", "entry-level")
    if level == "junior" and any(w in title for w in too_senior):
        return False
    if level == "senior" and any(w in title for w in too_junior):
        return False
    return True


def _check_needs_sponsorship(job, needs, profile):
    if not needs:                       # sponsorship nahi chahiye → koi rok nahi
        return True
    text = (job.get("description") or "").lower()
    blockers = ("no visa sponsorship", "unable to sponsor",
                "not able to sponsor", "no sponsorship",
                "must be authorized to work")
    return not any(b in text for b in blockers)


# constraint key  ->  check function
CHECKS = {
    "locations": _check_locations,
    "salary_floor": _check_salary_floor,
    "seniority": _check_seniority,
    "needs_sponsorship": _check_needs_sponsorship,
}


def passes(job: dict, profile: dict) -> bool:
    constraints = dict(profile.get("constraints", {}))
    # seniority profile ke top-level pe hai, usey bhi constraints me le aao
    if "seniority" in profile and "seniority" not in constraints:
        constraints["seniority"] = profile["seniority"]

    for key, value in constraints.items():
        check = CHECKS.get(key)          # jis constraint ka check nahi, skip
        if check and not check(job, value, profile):
            return False
    return True