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

from src.paths import FIT_MIN_OVERLAP


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


def profile_terms(profile: dict) -> set[str]:
    """Everything the person can honestly claim."""
    parts = []

    skills = profile.get("skills") or {}
    for tier in ("expert", "proficient", "familiar"):
        parts.extend(str(s) for s in (skills.get(tier) or []))

    for group in profile.get("skill_categories") or []:
        if isinstance(group, dict):
            parts.append(str(group.get("label", "")))
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


def overlap(requirements: list[str], profile: dict) -> tuple[float, set[str]]:
    """What fraction of what this job asks for can you honestly claim?

    Measured against the job's *requirements*, not its whole posting — a posting is
    mostly boilerplate about culture and benefits, and matching "collaborative" is
    not evidence you can do the work.
    """
    wanted = _terms(" ".join(requirements))
    if not wanted:
        return 1.0, set()               # nothing asked; nothing to fail

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

        super().__init__(
            f"This job asks for a background you do not have.\n\n"
            f"Only {overlap_pct}% of what \"{title}\"" 
            f"{f' at {company}' if company else ''} asks for appears anywhere in "
            f"your profile"
            f"{f' — just: {", ".join(sorted(matched)[:6])}' if matched else ''}.\n\n"
            f"A resume tailored to this job would have to invent the rest, and a "
            f"model asked to tailor it will: it will write you a career you never "
            f"had, fluently, and you would be the one sending it.\n\n"
            f"Nothing was generated. If you believe this is a good fit, the profile "
            f"is missing something — add it, and try again."
        )


def check_fit(job: dict, requirements: list[str], profile: dict) -> None:
    """Refuse before writing, not after.

    The guards downstream catch a fabricated resume. This stops one being
    attempted, which is better: a refusal you understand beats a refusal that
    arrives after the model has already tried twice and produced two lies.
    """
    score, matched = overlap(requirements, profile)
    if score < FIT_MIN_OVERLAP:
        raise JobDoesNotFitError(job, score, matched)
