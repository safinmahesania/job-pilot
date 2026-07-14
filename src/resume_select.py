"""The resume as a SELECTION from the profile, not a document written about it.

Every fabrication this project has produced came from the same place: the model was
asked to write something, and writing is where invention lives.

    - an employer it invented                — it was writing the experience section
    - a Business Administration degree       — it was writing the education section
    - "Proficient in React"                  — it was writing the summary
    - "(No work experience listed)"          — it was writing, and chose to write
                                               nothing

And every section where it was NOT asked to write has never once produced a lie.
The skills section takes a list of category labels and fills their contents from
profile.yaml. It has no guard. It has never needed one. A model that picks an item
from a list cannot pick an item that is not on it.

So the model stops writing.

It is handed the profile, numbered, and it returns numbers: which jobs in which
order, which of their bullets, which projects, which skills first. The code
assembles the page from the profile using those numbers. A bullet on the finished
resume is a bullet from profile.yaml, character for character, because there is no
step at which it could become anything else.

What this deletes:

    closed_lists()          — you cannot invent a fourth employer when you are
                              choosing an index into a list of three
    check_structured()      — there is nothing to check; an index is in range or it
                              is not
    the retry loop          — there is nothing to retry
    ~800 lines of guard     — guards against a thing that can no longer happen

What survives, and why:

    the summary             — the ONE place the model still writes, because it is
                              the one place where writing earns its keep: three
                              lines angled at this employer, drawn from the
                              paragraph you wrote and the skills you have. It keeps
                              its prose check, because it is still writing.

What this costs, honestly: your bullets are not reworded for each job. If the
posting says "REST APIs" and your bullet says "API-driven data", it stays as you
wrote it. That is a real loss and it is a small one — the skills section carries the
exact keywords already, and it is the only trade this whole design makes.
"""
import json
import re

from src.config import skill_groups
from src.paths import (
    RESUME_EXPERIENCE_BULLETS,
    RESUME_PROJECTS_USED,
    RESUME_PROJECT_BULLET_LINES,
)


def _dates(entry: dict) -> str:
    """"2024-05" and "Present" -> "May 2024 - Present"."""
    from src.apply import _span
    return _span(entry.get("start"), entry.get("end"))


def _date(entry: dict) -> str:
    from src.apply import _month_year
    return _month_year(entry.get("date"))


class MalformedResume(Exception):
    """The model returned something that is not a selection."""


def _numbered(items: list[str]) -> str:
    return "\n".join(f"  [{i}] {item}" for i, item in enumerate(items)) or "  (none)"


def choices(profile: dict) -> str:
    """The profile, numbered, as the model sees it.

    This is the whole world. There is nothing else to choose from, and nothing to
    add — which is not a rule the model has to follow, but a fact about the shape of
    the answer it can give.
    """
    blocks = []

    blocks.append("MY SKILL CATEGORIES — order them, most relevant to this job first.\n"
                  "All of them appear on the resume; you decide what comes first.\n"
                  + _numbered([g["label"] for g in skill_groups(profile)]))

    for section, label, describe in [
        ("experience", "MY JOBS", lambda e: (
            f"{e.get('role', '')} at {e.get('company', '')}")),
        ("education", "MY EDUCATION", lambda e: (
            f"{e.get('degree', '')}, {e.get('institution', '')}")),
        ("certificates", "MY CERTIFICATES", lambda e: e.get("name", "")),
        ("volunteer", "MY VOLUNTEER WORK", lambda e: (
            f"{e.get('organization', '')} — {e.get('role', '')}")),
    ]:
        entries = profile.get(section) or []
        lines = [f"{label} — every one of these appears on the resume. Order them."]
        lines.append(_numbered([describe(e) for e in entries]))

        if section == "experience":
            lines.append("\n  Each job's bullets, to choose from:")
            for i, entry in enumerate(entries):
                lines.append(f"  Job [{i}] — {entry.get('company', '')}:")
                for j, bullet in enumerate(entry.get("highlights") or []):
                    lines.append(f"      ({j}) {bullet}")

        blocks.append("\n".join(lines))

    projects = profile.get("projects") or []
    lines = [f"MY PROJECTS — pick the {RESUME_PROJECTS_USED} most relevant to this "
             f"job, best first."]
    for i, project in enumerate(projects):
        tech = ", ".join(str(t) for t in (project.get("tech") or []))
        lines.append(f"  [{i}] {project.get('name', '')}"
                     f"{f' ({tech})' if tech else ''}")
        for j, bullet in enumerate(project.get("highlights") or []):
            lines.append(f"      ({j}) {bullet}")
    blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def shape_for_prompt(profile: dict) -> str:
    """The answer, as a shape. Numbers, and one paragraph."""
    n_exp = len(profile.get("experience") or [])
    n_proj = len(profile.get("projects") or [])

    return json.dumps({
        "summary": "string — see the SUMMARY rule above",
        "skills": [f"integers 0..{len(skill_groups(profile)) - 1}, "
                   f"every one exactly once, most relevant first"],
        "experience": [{"job": f"integer 0..{max(n_exp - 1, 0)}",
                        "bullets": ["integers — which of THAT job's bullets to "
                                    "show, best first"]}],
        "education": ["integers — all of them, best first"],
        "projects": [{"project": f"integer 0..{max(n_proj - 1, 0)}",
                      "bullets": ["integers — which of THAT project's bullets"]}],
        "certificates": ["integers — all of them"],
        "volunteer": ["integers — all of them"],
    }, indent=2)


# ── Reading the answer ──────────────────────────────────────────────────────

def _ints(value, limit: int) -> list[int]:
    """The valid, in-range, non-repeating indices in whatever came back.

    An index the model made up is simply not in range, so it disappears. There is
    nothing to catch, refuse, or retry — a made-up number is not a made-up fact, it
    is just a number that does not point at anything.
    """
    out = []
    for item in value if isinstance(value, list) else []:
        try:
            i = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= i < limit and i not in out:
            out.append(i)
    return out


def parse(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise MalformedResume("no JSON object in the response")
        try:
            data = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError as e:
            raise MalformedResume(f"the JSON did not parse: {e}") from e

    if not isinstance(data, dict):
        raise MalformedResume("the response was not an object")
    return data


def resolve(selection: dict, profile: dict) -> dict:
    """Turn the model's numbers into the profile's own words.

    Everything the model left out is added back in the profile's order. Nothing real
    is dropped — not because the model was told not to drop it, but because dropping
    it is not one of the things it can do.
    """
    groups = skill_groups(profile)
    experience = profile.get("experience") or []
    education = profile.get("education") or []
    projects = profile.get("projects") or []
    certificates = profile.get("certificates") or []
    volunteer = profile.get("volunteer") or []

    def ordered(picked: list[int], total: int) -> list[int]:
        """The model's order, then everything it forgot, in the profile's order."""
        return picked + [i for i in range(total) if i not in picked]

    # ── Skills: order only. The contents are the profile's. ─────────────────
    skill_order = ordered(_ints(selection.get("skills"), len(groups)), len(groups))
    skills = [groups[i] for i in skill_order]

    # ── Experience: order, and which bullets. Every job appears. ────────────
    picked_jobs, chosen = [], {}
    for entry in selection.get("experience") or []:
        if not isinstance(entry, dict):
            continue
        job = _ints([entry.get("job")], len(experience))
        if not job or job[0] in picked_jobs:
            continue
        i = job[0]
        picked_jobs.append(i)
        available = len(experience[i].get("highlights") or [])
        chosen[i] = _ints(entry.get("bullets"), available)[:RESUME_EXPERIENCE_BULLETS]

    jobs = []
    for i in ordered(picked_jobs, len(experience)):
        entry = experience[i]
        highlights = entry.get("highlights") or []
        wanted = chosen.get(i) or list(range(min(len(highlights),
                                                 RESUME_EXPERIENCE_BULLETS)))
        jobs.append({
            "role": entry.get("role", ""),
            "company": entry.get("company", ""),
            "location": entry.get("location", ""),
            "dates": _dates(entry),
            "bullets": [highlights[j] for j in wanted],
        })

    # ── Projects: a genuine subset, and which bullets. ──────────────────────
    max_bullets = len(RESUME_PROJECT_BULLET_LINES)
    picked_projects, project_bullets = [], {}
    for entry in selection.get("projects") or []:
        if not isinstance(entry, dict):
            continue
        idx = _ints([entry.get("project")], len(projects))
        if not idx or idx[0] in picked_projects:
            continue
        i = idx[0]
        picked_projects.append(i)
        available = len(projects[i].get("highlights") or [])
        project_bullets[i] = _ints(entry.get("bullets"), available)[:max_bullets]

    chosen_projects = []
    for i in picked_projects[:RESUME_PROJECTS_USED]:
        entry = projects[i]
        highlights = entry.get("highlights") or []
        wanted = project_bullets.get(i) or list(range(min(len(highlights),
                                                          max_bullets)))
        chosen_projects.append({
            "name": entry.get("name", ""),
            "link": entry.get("link", ""),
            "bullets": [highlights[j] for j in wanted],
        })

    # ── The rest: order only. All of them appear. ───────────────────────────
    def in_order(selected, source, key):
        return [source[i] for i in ordered(_ints(selection.get(key), len(source)),
                                           len(source))]

    return {
        "summary": str(selection.get("summary") or "").strip(),
        "skills": skills,
        "experience": jobs,
        "education": [
            {"degree": e.get("degree", ""),
             "institution": e.get("institution", ""),
             "location": e.get("location", ""),
             "dates": _dates(e)}
            for e in in_order(selection, education, "education")
        ],
        "projects": chosen_projects,
        "certificates": [
            {"name": c.get("name", ""), "date": _date(c),
             "link": c.get("link", "")}
            for c in in_order(selection, certificates, "certificates")
        ],
        "volunteer": [
            {"organization": v.get("organization", ""),
             "role": v.get("role", ""),
             "description": v.get("description", "")}
            for v in in_order(selection, volunteer, "volunteer")
        ],
    }
