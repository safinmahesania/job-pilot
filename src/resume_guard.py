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
    out: dict = {}
    current = None
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
        out: list = []
        head = None
        body: list = []
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


# check_structured() lived here.
#
# It verified that every employer, school, project and certificate on the resume
# existed in the profile — because the model was writing them, and a model that
# writes an employer can write one you never worked for. It did, twice.
#
# The model no longer writes them. It returns an index into a list, and an index it
# invents is out of range and disappears. There is nothing left to verify, so there
# is nothing left here.
#
# check_prose() below survives, because the summary is still written, and writing is
# still where lies come from.


# ── Technologies named in prose ─────────────────────────────────────────────

def _profile_vocabulary(profile: dict) -> str:
    """Every word the profile contains, as one lowercase blob to search.

    Not a set of tokens: a blob, so that "REST" can be found inside "RESTful" and
    ".NET" inside ".NET 8" without either of them needing to be listed twice.
    """
    parts = [str(profile.get("summary") or "")]

    # skills comes in two shapes in the wild: a tiered dict
    # ({"expert": [...], "proficient": [...]}) and a flat list (["Python", ...]).
    # Only the dict form was read here, so a profile that used a plain list had an
    # empty skills vocabulary — and then every technology the cover letter named,
    # including ones straight off the person's own skills list, was flagged as
    # fabricated and the letter was refused every time. Both shapes are read now.
    skills = profile.get("skills") or {}
    if isinstance(skills, dict):
        for tier in skills.values():
            if isinstance(tier, (list, tuple)):
                parts.extend(str(s) for s in tier)
            else:
                parts.append(str(tier))
    elif isinstance(skills, (list, tuple)):
        parts.extend(str(s) for s in skills)
    else:
        parts.append(str(skills))

    for group in profile.get("skill_categories") or []:
        if isinstance(group, dict):
            parts.append(str(group.get("label", "")))
            parts.extend(str(s) for s in (group.get("skills") or []))

    for entry in profile.get("experience") or []:
        parts.append(str(entry.get("role", "")))
        parts.append(str(entry.get("company", "")))
        parts.extend(str(h) for h in (entry.get("highlights") or []))

    for entry in profile.get("projects") or []:
        parts.append(str(entry.get("name", "")))
        parts.extend(str(t) for t in (entry.get("tech") or []))
        parts.extend(str(h) for h in (entry.get("highlights") or []))

    for entry in profile.get("certificates") or []:
        parts.append(str(entry.get("name", "")))
        parts.append(str(entry.get("issuer", "")))

    # Education and volunteering were missing entirely, and so were the names of the
    # companies worked for — the vocabulary read an experience entry's ROLE and its
    # HIGHLIGHTS and stopped.
    #
    # Which meant a summary saying "teaching experience in Java at Concordia" was
    # refused: "Concordia" appears in summary, but it is nowhere in your profile. It
    # came from the job posting. You do not have it.
    #
    # It is in the profile. It is the university on the resume. The guard was looking
    # at a partial copy of the person and calling the missing parts lies — the same
    # shape of error as flagging his own surname, which was fixed, and this is the
    # rest of it.
    for entry in profile.get("education") or []:
        parts.append(str(entry.get("degree", "")))
        parts.append(str(entry.get("institution", "")))
        parts.extend(str(h) for h in (entry.get("highlights") or []))

    for entry in profile.get("volunteer") or []:
        parts.append(str(entry.get("organisation", "")))
        parts.append(str(entry.get("organization", "")))
        parts.append(str(entry.get("role", "")))
        parts.append(str(entry.get("description", "")))

    identity = profile.get("identity") or {}
    parts.extend(str(t) for t in (identity.get("titles") or []))

    return " ".join(parts).lower()


#: Words that begin a sentence and are capitalised for that reason alone. They are
#: not claims about anything.
_SENTENCE_STARTERS = {
    "a", "an", "the", "software", "junior", "senior", "experienced", "proficient",
    "skilled", "driven", "hands", "adept", "curious", "collaborative", "with",
    "built", "builds", "building", "developer", "engineer", "strong", "passionate",
    "motivated", "focused", "backend", "frontend", "full", "cross", "currently",
    "specialising", "specializing", "master", "bachelor", "computer", "science",
}


def named_technologies(text: str, sentence_start: bool = False) -> list[str]:
    """The technologies a piece of prose claims.

    `sentence_start` decides whether a capitalised word that OPENS a sentence counts.

    Reading a RESUME, it must not: "Software developer with..." begins with a capital
    because sentences do, and treating that as a claim about a tool called "Software"
    would fire on every honest summary ever written.

    Reading a JOB POSTING, it must: postings write "Python, Kotlin, React, GraphQL,
    Postgres, AWS." as a whole sentence, and skipping the first word threw away the
    first technology in every such list — which is how a Faire Product Engineer role
    came out as a 12% match for a Python developer.

    Same signal, two readers, opposite defaults.

    A resume sentence names a tool by capitalising it — React, Azure, PostgreSQL —
    or by punctuating it: C#, .NET, Node.js. Ordinary prose does not do either.
    That is enough to tell "Proficient in React" from "proficient at problem-
    solving" without needing a list of every technology in the world, which nobody
    has.

    Words that open a sentence are skipped: they are capitalised by grammar, not by
    claim.
    """
    found = []

    for sentence in re.split(r"(?<=[.!?])\s+", text or ""):
        words = sentence.split()
        for i, word in enumerate(words):
            # "SQL/NoSQL" is two claims, not one, and only one of them may be a
            # lie. "API-driven" is one claim and one ordinary word, and only the
            # claim is worth checking.
            for part in re.split(r"[/\-]", word):
                token = part.strip(",;:()").rstrip(".")
                if not token:
                    continue

                punctuated = any(c in token for c in "#+") or (
                    token.startswith(".") and len(token) > 1
                ) or (
                    "." in token[:-1] and not token[0].isdigit()
                )
                # A word after the first one in its sentence, capitalised. The "/"
                # split means "NoSQL" in "SQL/NoSQL" counts even though it is not
                # the first part.
                capitalised = token[0].isupper() and (
                    i > 0 or "/" in word or sentence_start)

                if not (punctuated or capitalised):
                    continue
                if token.lower() in _SENTENCE_STARTERS:
                    continue
                if len(token) < 2:
                    continue

                found.append(token)

    return found


def check_prose(resume: dict, profile: dict) -> list[str]:
    """Every technology named anywhere in the resume's prose must be one you have.

    The structured checks cover employers, schools, projects, certificates and skill
    labels — everything that lives in a FIELD. The prose was left alone, and so the
    prose is where the invention moved to:

        "Proficient in React, C# .NET, and full-stack feature delivery..."

    React is not in the profile. It is in the job description. Nothing was checking,
    so nothing stopped it, and a resume went out claiming a framework its owner has
    never used — the same failure as an invented employer, wearing prose instead of
    a field.
    """
    vocabulary = _profile_vocabulary(profile)

    # Your own name is not a technology.
    #
    # A model that writes "Safin Mahesania is a junior software developer..." gets
    # "Mahesania" read as a capitalised mid-sentence token, which is what a tool name
    # looks like — and then told, with total confidence, that it came from the job
    # posting and he does not have it. The verdict was accidentally right (a name
    # does not belong in a summary) and the reason was nonsense, and it burned three
    # retries arriving at it.
    #
    # The renderer strips the name anyway. This just stops the guard shouting about
    # a surname as though it were Kubernetes.
    ident = profile.get("identity") or {}
    own = {part.lower() for part in str(ident.get("name") or "").split() if part}

    problems = []
    seen = set()

    prose = [("summary", str(resume.get("summary") or ""))]
    for entry in resume.get("experience") or []:
        for bullet in entry.get("bullets") or []:
            prose.append((f"the {entry.get('company', 'experience')} bullets",
                          str(bullet)))
    for entry in resume.get("projects") or []:
        for bullet in entry.get("bullets") or []:
            prose.append((f"the {entry.get('name', 'project')} bullets",
                          str(bullet)))

    for where, text in prose:
        for token in named_technologies(text):
            key = (token.lower(), where)
            if key in seen:
                continue
            seen.add(key)

            # Substring both ways: "REST" is in "RESTful APIs"; ".NET" is in
            # ".NET 8". A tool you have does not need to be spelled identically.
            if token.lower() in vocabulary:
                continue
            if token.lower() in own:
                continue

            problems.append(
                f'"{token}" appears in {where}, but it is nowhere in your profile. '
                f'It came from the job posting. You do not have it.'
            )

    return problems


# The ordinary capitalised words a cover letter is full of that are not technologies:
# salutations and closings, and the generic vocabulary of applying for a job. Without
# this the guard reads "Dear Hiring Manager" as three invented tools. This is a
# stoplist, not an allowlist of tools — it only removes known non-tech English, so a
# real invented framework still gets through and flagged.
_COVER_LETTER_NON_TECH = {
    # salutation / closing
    "dear", "hi", "hello", "sincerely", "regards", "best", "kind", "warm",
    "thank", "thanks", "yours", "faithfully", "respectfully", "cheers",
    # the machinery of an application
    "hiring", "manager", "team", "recruiter", "recruiting", "position", "role",
    "job", "opening", "opportunity", "application", "applicant", "candidate",
    "company", "organisation", "organization", "department", "posting", "listing",
    "resume", "cv", "letter", "cover", "attached", "enclosed", "reference",
    # generic sentence words that get capitalised at the start of clauses
    "i", "my", "your", "our", "their", "this", "that", "these", "those", "it",
    "as", "at", "in", "on", "to", "for", "with", "and", "but", "or", "so",
    "when", "while", "where", "what", "who", "how", "why", "if", "then",
    "having", "being", "working", "looking", "seeking", "excited", "thrilled",
    "eager", "keen", "confident", "passionate", "motivated", "please", "would",
    "could", "should", "will", "am", "is", "are", "was", "were", "have", "has",
    "experience", "experienced", "skilled", "proficient", "background", "years",
    "year", "months", "recently", "currently", "graduate", "graduated", "student",
    "university", "college", "degree", "bachelor", "master", "diploma",
    "developer", "engineer", "programmer", "software", "development", "engineering",
    "junior", "senior", "intern", "internship", "entry", "level", "new", "grad",
    "projects", "project", "work", "worked", "building", "built", "developed",
    "created", "designed", "delivered", "shipped", "led", "managed", "collaborated",
}


def check_cover_letter_prose(text: str, profile: dict, target_company: str = "") -> list[str]:
    """Every technology a cover letter claims must be one you actually have.

    The resume was made safe by construction — it SELECTS from your background and can
    only reorder what is already true. The cover letter still WRITES prose, which is
    exactly where invention moved to once the resume closed the door:

        "In my three years with React and AWS at a fintech startup..."

    None of that is in the profile. It reads perfectly, it is a lie, and it goes to a
    named human who can check. This is the same grounding the resume summary gets: pull
    every technology the letter names, and if one is not anywhere in your profile, it
    came from the job description and you do not have it.

    A cover letter opens with capitalised sentences and names the target company by
    design, so both are excluded from the check — the company you are writing TO is not
    a claim about your background, and a sentence-initial capital is grammar, not a
    tool.
    """
    vocabulary = _profile_vocabulary(profile)

    ident = profile.get("identity") or {}
    own = {part.lower() for part in str(ident.get("name") or "").split() if part}

    # The company you are applying to is named all over a cover letter, legitimately.
    # It is not a claim about your history, so it must not read as an invented tool.
    company_words = {w.lower() for w in re.split(r"\W+", str(target_company)) if len(w) > 1}

    problems = []
    seen = set()
    for token in named_technologies(text, sentence_start=False):
        low = token.lower()
        if low in seen:
            continue
        seen.add(low)
        # A cover letter writes the company's name in the possessive constantly —
        # "PolicyMe's team", "Shopify's platform". The apostrophe-s is grammar, not a
        # different word, so strip it before checking: "policyme's" must match the
        # company "PolicyMe" just as "policyme" does.
        base = low[:-2] if low.endswith("'s") else low.rstrip("'")
        if (low in vocabulary or low in own or low in company_words
                or base in vocabulary or base in own or base in company_words):
            continue
        if low in _COVER_LETTER_NON_TECH or base in _COVER_LETTER_NON_TECH:
            continue
        problems.append(
            f'"{token}" appears in the cover letter, but it is nowhere in your '
            f'profile. It came from the job posting. You do not have it.'
        )

    return problems
