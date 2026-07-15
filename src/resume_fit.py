"""Some jobs cannot have a resume written for them, and that is not a bug.

Asked to tailor a computer science student's resume to a healthcare sales posting,
a model will produce a resume for a salesperson. It is not misbehaving. "Tailor
this resume to this job" has exactly one answer when the job and the person have
nothing in common, and that answer is fiction. The model was set an impossible
task and it did the only thing that satisfies it.

Three sessions were spent building guards to catch the resulting lies — the
invented employer, the invented degree, the skills lifted wholesale from the
posting. Every guard worked. Every guard was treating a symptom. The disease was
that the question should never have been asked.

So this module asks it first: can this resume be written honestly at all?

If the job wants a sales background and you have none, the honest resume for that
job does not exist — and JobPilot's job is to say so, not to produce something
plausible. A tool that quietly writes you a fictional sales career is worse than
one that refuses, because the fiction is what you send.

This is also, incidentally, information you wanted. "You cannot honestly apply to
this" is a better answer than a resume you would have to hide.
"""
import re

from src.paths import FIT_MIN_TECHNOLOGIES


def _terms(text: str) -> set[str]:
    """The meaningful words, lowercased. Not an embedding — a bag of words is
    enough to tell software engineering from healthcare sales, and it costs
    nothing and cannot hallucinate."""
    words = re.findall(r"[a-z0-9+#.]{2,}", (text or "").lower())
    return {w.strip(".") for w in words if w not in _STOPWORDS}


# Words that match everything and therefore mean nothing. "Experience in sales"
# and "experience in Flutter" share "experience" and "in" — counting those as
# evidence of fit is how a sales posting scores 8% instead of 0%.
_STOPWORDS = {
    # grammar
    "in", "on", "at", "to", "of", "or", "as", "by", "an", "is", "it", "be", "we",
    "and", "the", "for", "with", "you", "our", "are", "will", "have", "this",
    "that", "from", "your", "not", "all", "can", "has", "who", "any", "may",
    "must", "able", "into", "their", "them", "they", "its", "his", "her",
    # resume-speak that appears in every posting ever written
    "work", "working", "team", "teams", "role", "job", "years", "year",
    "experience", "experienced", "skills", "skill", "strong", "good", "great",
    "excellent", "ability", "including", "such", "other", "new", "well",
    "within", "across", "using", "use", "used", "plus", "etc", "eg", "ie",
    "per", "via", "knowledge", "understanding", "familiarity", "proficiency",
    "background", "environment", "based", "related", "similar", "level",
    "candidate", "candidates", "successful", "required", "preferred", "min",
    "minimum", "degree", "field", "equivalent", "responsibilities", "duties",
}


#: Skill categories that belong on the resume but are not evidence you can do the
#: work.
#:
#: The fit check exists to answer one question: would a resume for this job have to
#: lie? A sales posting wants communication, time management, MS Office and fluent
#: English — and so does every posting, and so does everyone. Counting them made a
#: "Canada Sales - Talent Community" posting a 15% match for a computer science
#: student, which is exactly the floor, on the strength of the words "office",
#: "management" and "communication".
#:
#: These stay on the resume, where a recruiter reads them and they mean something.
#: They do not count as proof you can write software.
_NOT_EVIDENCE = (
    "soft skill",
    "language",              # human languages: English, Urdu, French
    "productivity",          # MS Office, Slack — every job, every applicant
    "interpersonal",
    "communication",
    "personal",
)


def _is_evidence(label: str) -> bool:
    """Is this skill category evidence about the work, or about being a person?"""
    lowered = str(label).lower()
    # "Programming & Markup Languages" contains "language" and is very much
    # evidence. The distinction is whether the category is ABOUT programming.
    # "Software & Productivity Tools" is MS Office and Slack. "Developer Tools" is
    # Git and Postman. The word "tool" does not distinguish them; "developer" does.
    if any(word in lowered for word in ("programming", "markup", "framework",
                                        "database", "developer tool", "platform",
                                        "cloud", "machine learning", "methodolog",
                                        "architecture", "data")):
        return True
    return not any(word in lowered for word in _NOT_EVIDENCE)


def profile_terms(profile: dict) -> set[str]:
    """Everything the person can honestly claim AS EVIDENCE they can do the work.

    Not everything on their resume. Soft skills, human languages and office suites
    are real, belong on the page, and prove nothing about whether a software job is
    a fit — because everyone has them, including the people applying to the sales
    role.
    """
    parts: list = []

    skills = profile.get("skills") or {}
    for tier in ("expert", "proficient", "familiar"):
        parts.extend(str(s) for s in (skills.get(tier) or []))

    for group in profile.get("skill_categories") or []:
        if isinstance(group, dict):
            label = str(group.get("label", ""))
            if not _is_evidence(label):
                continue
            parts.append(label)
            parts.extend(str(s) for s in (group.get("skills") or []))

    for exp in profile.get("experience") or []:
        parts.append(str(exp.get("role", "")))
        parts.extend(str(h) for h in (exp.get("highlights") or []))

    for project in profile.get("projects") or []:
        parts.append(str(project.get("name", "")))
        parts.extend(str(t) for t in (project.get("tech") or []))
        parts.extend(str(h) for h in (project.get("highlights") or []))

    parts.append(str(profile.get("summary", "")))
    for title in (profile.get("identity") or {}).get("titles") or []:
        parts.append(str(title))

    return _terms(" ".join(parts))


def overlap(requirements: list[str], profile: dict,
            job: dict | None = None) -> tuple[float, set[str]]:
    """What fraction of what this job asks for can you honestly claim?

    Measured against the job's *requirements* where we have them — a posting is
    mostly boilerplate about culture and benefits, and matching "collaborative" is
    not evidence you can do the work.

    But requirement extraction is an LLM call, and it returns [] when it fails.
    This used to treat an empty list as "nothing asked, nothing to fail" and return
    a perfect score — so a failed extraction meant every job fit perfectly, and the
    sales posting sailed straight through the check built to stop it. A default
    that turns a failure into an approval is not a default; it is a hole.

    So when there are no requirements, read the technologies out of the posting
    directly.

    The first version of this fallback used the whole description as the
    requirement list, and its docstring said "noisier, but a sales posting is still
    unmistakably a sales posting". That was true and it was half the story. A job
    description is two thousand words of culture, benefits and encouragement with a
    dozen technologies buried in it, so the overlap drowns: a TD Bank software
    engineering internship — Java, Python, SQL, REST, Git — scored 11% against this
    profile and would have been REFUSED. The noise stops the sales job and it stops
    the right job too.

    named_technologies() already knows how to find the tools in a piece of prose,
    because it was written to catch a model claiming React. Pointed at the posting
    instead of the resume, it reads the same signal: TD Bank goes to 60%, Autodesk
    to 46%, and the sales posting to 0%, with no model call at all.
    """
    wanted = _terms(" ".join(requirements))

    if not wanted and job:
        from src.resume_guard import named_technologies

        raw = re.sub(r"<[^>]+>", " ", str(job.get("description", "")))
        # sentence_start=True: a posting writes "Python, Kotlin, React." as a whole
        # sentence, and the first word of it is a technology, not grammar.
        found = named_technologies(raw, sentence_start=True)

        # The title always counts: "Software Developer" and "Sales Representative"
        # are the single most honest words in a posting.
        wanted = _terms(" ".join(found + [str(job.get("title", ""))]))

    if not wanted:
        # Genuinely nothing to go on: no requirements, no title, no description.
        # Refusing here would block a job on the strength of no evidence at all.
        return 1.0, set()

    mine = profile_terms(profile)
    matched = wanted & mine
    return len(matched) / len(wanted), matched


class JobDoesNotFitError(Exception):
    """The honest resume for this job does not exist.

    Deliberately fatal. The alternative is a document that reads well, that you
    could send, and that describes someone else.
    """

    def __init__(self, job: dict, score: float, matched: set[str]):
        self.job = job
        self.score = score
        self.matched = matched

        title = job.get("title", "this role")
        company = job.get("company", "")
        overlap_pct = int(score * 100)

        # Assembled outside the f-strings below on purpose. Inlining
        #   f"...{", ".join(sorted(matched)[:6])}..."
        # with a double-quoted separator inside a double-quoted f-string relies on
        # PEP 701 quote-reuse, which is Python 3.12+ only — on 3.11 (what CI runs) the
        # outer quote closes early and the module fails to import. Building the pieces
        # here keeps every f-string simple and the syntax valid on both.
        at_company = f" at {company}" if company else ""
        matched_list = ", ".join(sorted(matched)[:6])
        just = f" — just: {matched_list}" if matched else ""

        super().__init__(
            f"This job asks for a background you do not have.\n\n"
            f'Only {overlap_pct}% of what "{title}"'
            f"{at_company} asks for appears anywhere in "
            f"your profile"
            f"{just}.\n\n"
            f"A resume tailored to this job would have to invent the rest, and a "
            f"model asked to tailor it will: it will write you a career you never "
            f"had, fluently, and you would be the one sending it.\n\n"
            f"Nothing was generated. If you believe this is a good fit, the profile "
            f"is missing something — add it, and try again."
        )


def check_fit(job: dict, requirements: list[str], profile: dict) -> None:
    """Refuse before writing, not after.

    The guards downstream catch a fabricated resume. This stops one being attempted,
    which is better: a refusal you understand beats a refusal that arrives after the
    model has already tried twice and produced two lies.

    HOW IT DECIDES, and why it changed.

    This used to be a ratio: of everything the posting asked for, what fraction was
    in the profile. It was tuned three times and it never worked, because the
    denominator is a job description — two thousand words of culture, benefits and
    section headings with a dozen technologies buried in them. Real developer jobs
    came out at 10-18% against a 15% floor. The measure had no power left to
    discriminate; it was reporting noise, and the floor was cutting through the
    middle of it. Its matched terms were "what", "every", "day", "best".

    A ratio was the wrong instrument. This check has exactly one job — refuse a job
    whose resume would have to lie — and that is not a question of degree. It is:
    IS THIS JOB IN MY FIELD?

    So: count. How many of MY technologies does this posting name? The list of my
    technologies has no noise in it, because a person wrote it. There is no
    denominator to be poisoned.

        sales, recruiting, sales rep         0
        IT operations                        1
        TD Bank software engineering         4  (Java, Python, SQL, Git)
        Geotab software developer            5  (C#, .NET, SQL Server, Azure, REST)

    The separation is not close, and it does not need tuning.
    """
    # A bare title, and nothing else.
    #
    # There is no evidence here to judge, and refusing on no evidence is a different
    # error from the one this check exists to prevent — it would block a job whose
    # posting is merely vague. A title cannot be trusted to carry the decision on its
    # own: "Software Engineer" names no language, and neither does "Engineer II", and
    # both are jobs he should see.
    said = f"{job.get('description', '')} {' '.join(requirements or [])}"
    if not said.strip():
        return

    wanted = technologies_wanted(job, profile, requirements)
    languages = wanted & _LANGUAGES
    fields = wanted & my_fields(profile)

    # A job that asks for a programming language YOU WRITE is a job you can write an
    # honest resume for. You may be underqualified for it — that is a different
    # question, and not this one's business — but there is nothing you would have to
    # invent.
    #
    # This is what a count alone could not see. DoorDash's backend role names Kotlin,
    # gRPC, Postgres, Kubernetes and AWS, of which he has none, and Java, which he
    # has. One match. A count of two refused it — a Java backend job, refused to a
    # Java developer. Meanwhile an IT operations role also scored one, on the word
    # "Agile", and a network engineering role scored one on "Azure".
    #
    # The difference is not how many. It is WHICH.
    if languages or fields:
        return

    # No language of yours in it. Two other tools of yours can still make it a job in
    # your field — a DevOps role naming Azure and Docker, say. One cannot: "Agile" is
    # in every posting ever written.
    if len(wanted) >= FIT_MIN_TECHNOLOGIES:
        return

    score, _ = overlap(requirements, profile, job)
    raise JobDoesNotFitError(job, score, wanted)

# ── How many of YOUR technologies does this job ask for? ────────────────────

def my_technologies(profile: dict) -> set[str]:
    """The tools you have, as YOU listed them.

    A curated list, written by a person, with no filler in it — which is exactly
    what the other side of this comparison never was.
    """
    found = set()

    skills = profile.get("skills") or {}
    for tier in ("expert", "proficient", "familiar"):
        for skill in skills.get(tier) or []:
            found.add(str(skill).lower().strip())

    for group in profile.get("skill_categories") or []:
        if not isinstance(group, dict):
            continue

        label = str(group.get("label", ""))
        if not _is_evidence(label):
            continue

        for skill in group.get("skills") or []:
            found.add(str(skill).lower().strip())

        # The label itself, as a FIELD — see my_fields() below.
        for phrase in re.split(r"[&,/]", label):
            phrase = phrase.strip().lower()
            if len(phrase.split()) >= 2:
                found.add(phrase)

    for project in profile.get("projects") or []:
        for tech in project.get("tech") or []:
            found.add(str(tech).lower().strip())

    # "MS Office (Word, Excel, PowerPoint)" is one entry and no technology.
    return {f for f in found if f and len(f) > 1}


def technologies_wanted(job: dict, profile: dict,
                        requirements: list[str] | None = None) -> set[str]:
    """Which of them this posting names.

    The extracted requirements come first when there are any — a clean list from the
    model beats reading the raw posting, and throwing it away was a real bug: the
    first version of this read only the description, so a caller that had already
    done the extraction had it silently ignored.

    The posting itself is always read too. The model's list is a summary, and a
    summary can drop a language.
    """
    blob = re.sub(r"<[^>]+>", " ",
                  f"{job.get('title', '')} {job.get('description', '')} "
                  f"{' '.join(requirements or [])}").lower()

    wanted = set()
    for tech in my_technologies(profile):
        # Whole-token, so "c" does not match "critical" and "r" does not match
        # "revenue" — the single-letter languages are why this needs a boundary and
        # not an `in`.
        if re.search(rf"(?<![a-z0-9+#.]){re.escape(tech)}(?![a-z0-9+#])", blob):
            wanted.add(tech)
    return wanted


#: Programming languages, as a set. Not "every technology" — that list does not
#: exist and could not be maintained. Languages are a small, slow-moving set, and
#: they are the one thing that says what KIND of work a job is.
_LANGUAGES = {
    "python", "java", "javascript", "typescript", "c", "c#", "c++", "go", "golang",
    "rust", "ruby", "php", "swift", "kotlin", "dart", "scala", "perl", "r",
    "objective-c", "sql", "html", "css", "bash", "shell", "matlab", "julia",
    "haskell", "elixir", "erlang", "clojure", "lua", "assembly", "vb.net",
    "visual basic", "cobol", "fortran", "groovy", "solidity",
}


def my_languages(profile: dict) -> set[str]:
    """The programming languages you write."""
    return {t for t in my_technologies(profile) if t in _LANGUAGES}


def languages_wanted(job: dict, profile: dict,
                     requirements: list[str] | None = None) -> set[str]:
    return technologies_wanted(job, profile, requirements) & _LANGUAGES


def my_fields(profile: dict) -> set[str]:
    """The FIELDS you work in, from the labels you wrote on your own skills.

    "Machine Learning & Deep Learning" is a field. "Azure" is a tool.

    The difference matters, and a count cannot see it. An Achievers data science
    posting names no tool at all — "innovative AI and Machine Learning based
    approaches", "a strong background in applied Machine Learning", "models that
    power real-world solutions" — and matched exactly one thing: the field. A
    technical support role also matched exactly one thing: Azure. Counted, they are
    identical. They are not identical.

    A FIELD says what KIND of work a job is, the way a language does. A TOOL does
    not — every posting mentions a cloud.

    Refusing the Achievers role was wrong: there is a PyTorch classifier with 99.8%
    accuracy sitting in the profile, and the resume would have been entirely honest.
    """
    fields = set()
    for group in profile.get("skill_categories") or []:
        if not isinstance(group, dict):
            continue
        label = str(group.get("label", ""))
        if not _is_evidence(label):
            continue
        for phrase in re.split(r"[&,/]", label):
            phrase = phrase.strip().lower()
            # Two words or more. "Databases", "Cloud" and "Architecture" alone appear
            # in every posting ever written; counting them puts the noise straight
            # back.
            if len(phrase.split()) >= 2:
                fields.add(phrase)
    return fields


def fields_wanted(job: dict, profile: dict,
                  requirements: list[str] | None = None) -> set[str]:
    return technologies_wanted(job, profile, requirements) & my_fields(profile)
