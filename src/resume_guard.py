"""Two guards that stand between you and a fabricated resume.

A model handed an empty profile, a template full of {{EXPERIENCE}} placeholders,
and a job description will fill the template from the job description. It is not
being disobedient — it is being asked to write a resume and given exactly one
source of facts, and that source is the posting. It will invent an employer, a
degree and a name, and the result will be fluent, plausible, and a complete
fabrication that you might send to a real company.

No prompt survives this. "Never invent an employer" is a rule with nothing behind
it when the model has nothing else to write from. So:

  1. BEFORE generating — refuse if the profile cannot support the template. If
     there is no experience and no project in profile.yaml, there is no resume to
     write, and asking for one anyway is asking to be lied to.

  2. AFTER generating — check that every employer, school, project and certificate
     in the output actually appears in the profile. Anything that doesn't was
     invented, and a resume with an invented employer is not a draft to be tidied
     up. It is refused.

The second check is what makes the first one honest. A profile can pass validation
and the model can still drift, and the only way to know is to look at what came
back and compare it to what went in.
"""
import re

# What a resume cannot be written without.
REQUIRED = {
    "identity.name": "your name",
    "summary": "a summary paragraph",
    "education": "at least one education entry",
    "skill_categories": "your skills",
}


# Text that means "I have not written this yet". A placeholder left in the profile
# is worse than a missing field: a missing field is simply absent from the resume,
# but a placeholder is handed to the model as though it were a fact. It will either
# print "TODO — a third point" on your resume, or — worse — see the gap it names and
# fill it. Inventing content to satisfy a TODO is exactly the failure this module
# exists to prevent.
PLACEHOLDERS = ("todo", "tbd", "fixme", "xxx", "lorem ipsum",
                "your first point", "your second point", "notable outcome or scale")


def _placeholder_text(value) -> bool:
    lowered = str(value).strip().lower()
    return any(lowered.startswith(p) or f" {p}" in lowered for p in PLACEHOLDERS)


def find_placeholders(profile: dict) -> list[str]:
    """Anything in the profile you meant to come back and write."""
    found = []

    for section in ("experience", "projects"):
        for entry in profile.get(section) or []:
            if not isinstance(entry, dict):
                continue
            label = entry.get("name") or entry.get("role") or "?"
            for i, point in enumerate(entry.get("highlights") or []):
                if _placeholder_text(point):
                    found.append(
                        f'{section}[{label}] point {i + 1} is still a placeholder: '
                        f'"{str(point)[:60]}"'
                    )

    for entry in profile.get("volunteer") or []:
        if isinstance(entry, dict) and _placeholder_text(entry.get("description", "")):
            found.append(
                f'volunteer[{entry.get("organization", "?")}] description is still '
                f'a placeholder.'
            )

    if _placeholder_text(profile.get("summary", "")):
        found.append("summary is still a placeholder.")

    return found


def validate_profile(profile: dict) -> list[str]:
    """What's missing from profile.yaml. An empty list means it's ready.

    Being strict here is the point. A half-filled profile does not produce a
    half-filled resume — it produces a fully-filled invented one.
    """
    missing = []

    ident = profile.get("identity") or {}
    if not ident.get("name"):
        missing.append("identity.name — your name")

    if not (profile.get("summary") or "").strip():
        missing.append("summary — a short paragraph about you")

    if not (profile.get("education") or []):
        missing.append("education — at least one entry")

    if not (profile.get("skill_categories") or profile.get("skills")):
        missing.append("skill_categories — your skills")

    # Experience or projects. Not both — a student may genuinely have no jobs yet,
    # and a career changer may have no side projects. But not neither: with
    # neither, there is nothing to write a resume about.
    if not (profile.get("experience") or []) and not (profile.get("projects") or []):
        missing.append(
            "experience or projects — at least one of them. With neither, there "
            "is nothing for the resume to describe, and the model will fill the "
            "gap from the job posting."
        )

    missing.extend(find_placeholders(profile))

    return missing


# ── Grounding: is everything in the resume actually yours? ──────────────────

def _normalise(text: str) -> str:
    """Lowercase, strip punctuation and collapse spaces, so "Acme Corp." and
    "Acme Corp" are the same company."""
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).strip()


def known_entities(profile: dict) -> dict[str, set[str]]:
    """Every proper noun the resume is allowed to contain."""
    def norm_all(values):
        return {_normalise(v) for v in values if v and _normalise(v)}

    ident = profile.get("identity") or {}

    return {
        "name": norm_all([ident.get("name")]),
        "company": norm_all(
            e.get("company") for e in (profile.get("experience") or [])
        ),
        "institution": norm_all(
            e.get("institution") for e in (profile.get("education") or [])
        ),
        "project": norm_all(
            p.get("name") for p in (profile.get("projects") or [])
        ),
        "certificate": norm_all(
            c.get("name") for c in (profile.get("certificates") or [])
        ),
        "organisation": norm_all(
            v.get("organization") for v in (profile.get("volunteer") or [])
        ),
    }


def _mentions_any(line: str, allowed: set[str]) -> bool:
    """Does this line name something from the profile?

    Substring both ways: the resume line may be "Acme Corp, Toronto, ON" while the
    profile says "Acme Corp", or the resume may abbreviate.
    """
    text = _normalise(line)
    if not text:
        return True                     # nothing claimed, nothing to check
    return any(known in text or text in known for known in allowed if known)


def _sections(markdown: str) -> dict[str, list[str]]:
    out, current = {}, None
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            current = line[3:].strip().lower()
            out[current] = []
        elif current is not None:
            out[current].append(line)
    return out


def check_grounding(markdown: str, profile: dict) -> list[str]:
    """Everything in the resume that is not in the profile.

    An empty list means every employer, school, project and certificate on the
    page is one you actually have.
    """
    known = known_entities(profile)
    problems = []

    # ── The name ────────────────────────────────────────────────────────────
    # This is the tell. When the model invents a person, the name it invents is
    # usually lifted straight from the job title.
    for line in markdown.splitlines():
        if line.startswith("# "):
            name = line[2:].strip()
            if "{{" in name:            # placeholder, substituted later — fine
                break
            if not _mentions_any(name, known["name"]):
                problems.append(
                    f'The name on this resume is "{name}", which is not yours. '
                    f'The model invented a person.'
                )
            break

    sections = _sections(markdown)

    def entries(section: str) -> list[tuple[str, list[str]]]:
        """[(heading, [the lines under it that aren't bullets]), ...]"""
        out, head, body = [], None, []
        for line in sections.get(section, []):
            if line.startswith("### "):
                if head is not None:
                    out.append((head, body))
                head, body = line[4:].split("@@")[0].strip(), []
            elif head is not None and not line.lstrip().startswith(("- ", "* ")):
                if line.strip():
                    body.append(line.strip())
        if head is not None:
            out.append((head, body))
        return out

    # ── Employers ───────────────────────────────────────────────────────────
    for role, lines in entries("work experience"):
        company_line = lines[0] if lines else ""
        if not _mentions_any(company_line, known["company"]):
            problems.append(
                f'"{company_line or role}" is not an employer in your profile. '
                f'You never worked there.'
            )

    # ── Schools ─────────────────────────────────────────────────────────────
    for degree, lines in entries("education"):
        school_line = lines[0] if lines else ""
        if not _mentions_any(school_line, known["institution"]):
            problems.append(
                f'"{school_line or degree}" is not in your education. '
                f'You did not study there.'
            )

    # ── Projects ────────────────────────────────────────────────────────────
    for name, _ in entries("projects"):
        # "JobPilot - Personal (Python, FastAPI)" -> "JobPilot"
        bare = re.split(r"\s+[-–—(]", name)[0].strip()
        if not _mentions_any(bare, known["project"]):
            problems.append(f'"{bare}" is not a project in your profile.')

    # ── Certificates ────────────────────────────────────────────────────────
    for line in sections.get("certificates and achievements", []):
        if not line.lstrip().startswith(("- ", "* ")):
            continue
        cert = line.lstrip()[2:].split("@@")[0].strip()
        if not _mentions_any(cert, known["certificate"]):
            problems.append(f'"{cert}" is not a certificate you hold.')

    # ── Volunteer ───────────────────────────────────────────────────────────
    for org, _ in entries("volunteer and community involvement"):
        if not _mentions_any(org, known["organisation"]):
            problems.append(f'"{org}" is not in your volunteer history.')

    # ── Skills ──────────────────────────────────────────────────────────────
    # The skill LINES are yours; their labels come from profile.yaml. A resume
    # headed "Sales Experience:" for a profile that has no such category did not
    # get that from you — it got it from the job posting, which is the same failure
    # as an invented employer wearing different clothes.
    from src.config import skill_groups
    allowed = {_normalise(g["label"]) for g in skill_groups(profile)}
    if allowed:
        for line in sections.get("skills", []):
            match = re.match(r"[-*]\s*\*\*(.+?):?\*\*", line.strip())
            if not match:
                continue
            label = match.group(1).strip()
            if not _mentions_any(label, allowed):
                problems.append(
                    f'"{label}" is not a skill category in your profile. It came '
                    f'from the job posting, not from you.'
                )

    # ── Nothing real may be dropped ─────────────────────────────────────────
    # The other half of the failure. Told it had invented an employer, the model
    # deleted the entire Work Experience section and wrote "(No work experience
    # listed)" — for someone with three real jobs. Over-correction is not a fix;
    # a resume that omits your career is as wrong as one that invents a career.
    for label, section, allowed_set in [
        ("Work Experience", "work experience", known["company"]),
        ("Education", "education", known["institution"]),
    ]:
        if allowed_set and not entries(section):
            problems.append(
                f'The {label} section is empty, but your profile has '
                f'{len(allowed_set)}. Nothing real may be dropped — list them.'
            )

    return problems


class FabricationError(Exception):
    """The model invented facts. The resume is not returned.

    This is deliberately fatal rather than a warning. A resume with an invented
    employer is not a draft you tidy up — one careless send and you are explaining
    to a recruiter why you claimed to work somewhere you didn't. There is no
    version of that which ends well, so the document does not reach you at all.
    """

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__(
            "The generated resume contains facts that are not in your profile:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )


class ProfileIncompleteError(Exception):
    """profile.yaml cannot support a resume. Nothing was generated."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(
            "config/profile.yaml is missing what a resume needs:\n"
            + "\n".join(f"  - {m}" for m in missing)
            + "\n\nWithout these the model has only the job posting to write from, "
              "and it will write from it — inventing an employer, a degree and a "
              "name. Fill these in first."
        )


# ── Grounding, on the structure rather than on prose ────────────────────────

def check_structured(resume: dict, profile: dict) -> list[str]:
    """Every fact in the resume, checked against the profile. No parsing.

    The markdown version of this had to infer the structure back out of the text,
    and it got it wrong: a model that wrote "**Teaching Assistant** | Concordia"
    instead of "### Teaching Assistant" produced a resume the parser read as empty,
    and an honest document was refused for a formatting difference. A check that
    fails on formatting tells you nothing about truth.

    Here the structure is given. "Is this employer mine" is a dictionary lookup.
    """
    known = known_entities(profile)
    problems = []

    def check(items, field, allowed, label, noun):
        for item in items:
            value = str(item.get(field, "")).strip()
            if not value:
                continue
            if not _mentions_any(value, allowed):
                problems.append(f'"{value}" is not {noun}.')

    check(resume.get("experience") or [], "company", known["company"],
          "Work Experience", "an employer in your profile — you never worked there")
    check(resume.get("education") or [], "institution", known["institution"],
          "Education", "in your education — you did not study there")
    check(resume.get("projects") or [], "name", known["project"],
          "Projects", "a project in your profile")
    check(resume.get("certificates") or [], "name", known["certificate"],
          "Certificates", "a certificate you hold")
    check(resume.get("volunteer") or [], "organization", known["organisation"],
          "Volunteer", "in your volunteer history")

    # Skill category labels must be the profile's own.
    #
    # The model returns a list of LABELS now — plain strings, in the order it wants
    # them shown — because the contents come from the profile and there was never a
    # reason to let it retype them. This crashed on the first real job after the
    # change: the shape moved and this check did not, and it called .get() on a
    # string. That is the cost of a schema that lives in two places, so it is
    # tolerant of both shapes rather than assuming either.
    from src.config import skill_groups
    allowed_labels = {_normalise(g["label"]) for g in skill_groups(profile)}
    if allowed_labels:
        for group in resume.get("skills") or []:
            if isinstance(group, dict):
                label = str(group.get("label", "")).strip()
            else:
                label = str(group).strip()

            if label and not _mentions_any(label, allowed_labels):
                problems.append(
                    f'"{label}" is not a skill category in your profile. It came '
                    f'from the job posting, not from you.'
                )

    # Nothing real may be dropped. A missing section is now a missing KEY — plain
    # to see — where in markdown it was indistinguishable from a heading the model
    # had formatted its own way.
    for key, label, have in [
        ("experience", "Work Experience", len(profile.get("experience") or [])),
        ("education", "Education", len(profile.get("education") or [])),
    ]:
        if have and not (resume.get(key) or []):
            problems.append(
                f"The {label} section is empty, but your profile has {have}. "
                f"A job that does not value your experience does not erase it."
            )

    return problems
