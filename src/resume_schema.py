"""The resume as data, not as text the model has to format correctly.

The old design asked the model for markdown in a precise convention: `###` for an
entry, `@@` to push a date right, `- ` for a bullet, a blank line between sections.
Then it verified that markdown by parsing it, and refused the resume when the parse
came back empty.

Which it did, regularly — not because the model had invented anything, but because
it had written

    **Teaching Assistant** | Concordia University        Jan 2026 - Apr 2026

instead of

    ### Teaching Assistant @@ Jan 2026 - Apr 2026
    Concordia University

Identical to a reader. Invisible to the parser. The guard reported "your Work
Experience section is empty" for a resume that had three jobs in it, and refused a
document that was perfectly honest. A verification that fails on formatting cannot
tell you anything about truth.

So: the model no longer writes the document. It fills in fields. It is handed a
JSON shape and returns the same shape with the content chosen and worded for this
job, and the code renders the page. Nothing is parsed back out of prose, because
nothing was ever put into prose.

Everything downstream gets easier the moment the structure is real rather than
inferred:

  * Grounding is a comparison of strings, not a regex over markdown. "Is this
    employer in the profile" is a dictionary lookup.
  * A dropped section is a missing key, which is unmissable — where in markdown it
    was indistinguishable from a heading the model formatted differently.
  * Length limits apply to the field they belong to.
  * The layout — small caps, right-aligned dates, the hairline rule — belongs
    entirely to the renderer, where it can be tested, and not at all to the model,
    where it could only be requested.
"""
import json
import re

from src.config import skill_groups


#: What the model is asked to return. Every key required; empty lists allowed only
#: where the profile is genuinely empty.
SHAPE = {
    "summary": "string — 2-3 sentences, angled at this job. NEVER write my name, "
               "and never write \"I\", \"he\" or \"she\". A resume summary is a "
               "headline, not a biography: it opens with the noun. \"Junior "
               "software developer with experience across frontend and backend...\" "
               "— NOT \"Safin Mahesania is a junior software developer...\". My "
               "name is already at the top of the page in 20-point type. "
               "Name ONLY technologies that appear in MY PROFILE. If the job wants "
               "React and I do not have React, I do not have React — do not write "
               "it. Lead with what I DO have that this job also wants.",
    "skills": ["string — a skill category LABEL from my profile, verbatim. "
               "Order them most relevant first. The contents are filled in from "
               "my profile; you choose only which categories appear and in what "
               "order. Do not write the skills themselves."],
    "experience": [{"role": "string", "company": "string",
                    "location": "string", "dates": "string — e.g. May 2024 - Aug 2024",
                    "bullets": ["string", "..."]}],
    "education": [{"degree": "string", "institution": "string",
                   "location": "string", "dates": "string"}],
    "projects": [{"name": "string", "owner": "string", "tech": ["string"],
                  "link": "string", "bullets": ["string", "..."]}],
    "certificates": [{"name": "string", "date": "string", "link": "string"}],
    "volunteer": [{"organization": "string",
                   "description": "string — 1-2 sentences. Work my ROLE into the "
                                  "sentence rather than labelling it: write "
                                  "\"As STEM Co-Lead, organised...\" or \"Led a "
                                  "team that...\", not a title on its own line. "
                                  "The role is in my profile; make it read like "
                                  "prose, not a form."}],
}


def shape_for_prompt() -> str:
    return json.dumps(SHAPE, indent=2)


class MalformedResume(Exception):
    """The model returned something that is not a resume."""


def parse(text: str) -> dict:
    """Pull the JSON object out of whatever the model wrapped it in."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Some models prepend a sentence. Take the outermost object.
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise MalformedResume("no JSON object in the response")
        try:
            data = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError as e:
            raise MalformedResume(f"the JSON did not parse: {e}") from e

    if not isinstance(data, dict):
        raise MalformedResume("the response was not an object")

    # Normalise: a missing list is an empty list, never a crash later.
    for key in ("skills", "experience", "education", "projects",
                "certificates", "volunteer"):
        value = data.get(key)
        data[key] = value if isinstance(value, list) else []

    data["summary"] = str(data.get("summary") or "").strip()
    return data


def _short_url(url: str) -> str:
    """"https://www.linkedin.com/in/safinmahesania/" is a URL. "linkedin.com/in/
    safinmahesania" is a person. The scheme, the www and the trailing slash carry
    no information and cost a third of the line."""
    text = str(url or "").strip()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^www\.", "", text)
    return text.rstrip("/")


def contact_line(profile: dict) -> list[str]:
    """The header, built in ONE place.

    It used to be built in two: here for the plain path, and again inside
    fill_contact() for the redacted one, where a {{LINKS}} placeholder joined the
    raw URLs with a middle dot. Only this one shortened them, only this one used
    the pipe the renderer splits on, and only this one produced anything the link
    labeller could recognise — so in redacted mode, which is the default, none of
    that work ran and the LinkedIn URL quietly vanished into a string the renderer
    could not read.

    Two builders for one line is one builder too many.
    """
    contact = profile.get("contact") or {}

    where = ", ".join(
        part for part in (contact.get("city"), contact.get("province"),
                          contact.get("country")) if part
    )
    rest = [
        contact.get("email"),
        contact.get("phone"),
        _short_url(contact.get("linkedin")),
        _short_url(contact.get("github")),
        _short_url(contact.get("website")),
    ]
    return [where, " | ".join(p for p in rest if p)]


def _contact_line(profile: dict, redacted: bool) -> list[str]:
    # Redacted or not, the line has the same shape. The difference is only WHERE it
    # is filled in — before the model sees it, or after — and that is fill_contact's
    # business, not the renderer's.
    if redacted:
        return ["{{LOCATION}}", "{{CONTACT}}"]
    return contact_line(profile)


def _headline(summary: str, name: str, profile: dict) -> str:
    """A resume summary is a headline, not a biography.

    The model wrote: "Safin Mahesania is a junior software developer with experience
    in both frontend and backend development. With a background in Java, Flutter and
    Python, Safin Mahesania excels at integrating APIs..." — the name twice, in the
    third person, on a page where the name is already the largest thing on it.

    It was invited to. In redacted mode the fact sheet hands the model "{{NAME}}" and
    tells it to write that wherever a name belongs, which reads as permission to put
    one in the prose. The substitution then turns it into a real name, and the resume
    introduces you to yourself.

    The prompt now says not to. This makes sure of it: strip the name, strip the
    placeholder, strip the "is a" left behind, and let the sentence start with the
    noun, where it belonged all along.
    """
    if not summary:
        return ""

    real = str((profile.get("identity") or {}).get("name") or "").strip()
    for token in filter(None, [name, real, "{{NAME}}"]):
        summary = summary.replace(token, "")

    # "  is a junior software developer" -> "Junior software developer"
    summary = re.sub(r"^\s*(?:is|was)\s+(?:an?\s+)?", "", summary.strip())
    # "With a background in X, , excels at Y" -> "..., excels at Y"
    summary = re.sub(r",\s*,", ",", summary)
    # Tidy " ," and " ." left by the removal — but NOT the space before ".NET",
    # ".js" or any other name that legitimately begins with a dot. A period is only
    # sentence punctuation when a space or the end of the string follows it.
    summary = re.sub(r"\s+([,;])", r"\1", summary)
    summary = re.sub(r"\s+\.(?=\s|$)", ".", summary)
    summary = re.sub(r"\s{2,}", " ", summary).strip()

    if summary:
        summary = summary[0].upper() + summary[1:]
    return summary


def to_markdown(resume: dict, profile: dict, name: str,
                redacted: bool = False) -> str:
    """Render the structured resume to the markdown the docx and PDF renderers eat.

    This is the ONLY place the document's shape is decided. The model never sees a
    `###` or an `@@` — it cannot get them wrong, because it is never asked for them.
    """
    out = [f"# {name}", ""]
    out.extend(_contact_line(profile, redacted))
    out.append("")

    summary = _headline(str(resume.get("summary") or "").strip(), name, profile)
    if summary:
        out += ["## Summary", "", summary, ""]

    # Skills come from the profile, not from the model.
    #
    # There was never a reason to ask for them. The categories and their contents
    # are a fixed list in profile.yaml — the model's only useful contribution is
    # deciding which ones this employer should see first. Asking it to reproduce
    # the contents as well gave it the chance to get them wrong, and it did: it
    # copied the fact sheet's own annotation and printed "Skill category —
    # Programming & Markup Languages" on the page.
    #
    # So it picks labels. The code fills them.
    groups = {str(g["label"]): g["skills"] for g in skill_groups(profile)}

    def label_of(entry):
        """The model returns strings. An older shape returned {"label": ...}, and a
        model given a JSON example will occasionally reach for it anyway. Reading
        both costs one line; guessing wrong loses the ordering silently."""
        return str(entry.get("label", "") if isinstance(entry, dict) else entry)

    ordered = [label_of(e) for e in (resume.get("skills") or [])
               if label_of(e) in groups]
    # Anything the model left out is still yours, so it still appears. Dropping a
    # skill category is dropping a fact.
    ordered += [label for label in groups if label not in ordered]

    if ordered:
        out += ["## Skills", ""]
        for label in ordered:
            skills = " | ".join(str(s) for s in groups[label])
            if skills:
                out.append(f"- **{label}:** {skills}")
        out.append("")

    if resume.get("education"):
        out += ["## Education", ""]
        for entry in resume["education"]:
            degree = entry.get("degree", "")
            out.append(f"### {degree} @@ {entry.get('dates', '')}".rstrip(" @"))
            where = ", ".join(p for p in (entry.get("institution"),
                                          entry.get("location")) if p)
            if where:
                out.append(where)
            out.append("")

    if resume.get("experience"):
        out += ["## Work Experience", ""]
        for entry in resume["experience"]:
            out.append(f"### {entry.get('role', '')} @@ {entry.get('dates', '')}"
                       .rstrip(" @"))
            where = ", ".join(p for p in (entry.get("company"),
                                          entry.get("location")) if p)
            if where:
                out.append(where)
            for bullet in entry.get("bullets") or []:
                out.append(f"- {bullet}")
            out.append("")

    if resume.get("projects"):
        out += ["## Projects", ""]
        for entry in resume["projects"]:
            # The name, and nothing else. The owner and the tech stack were both
            # printed here once — "Recipedia - Course - Course (Flutter, Dart,
            # Firebase, Python, SQLite, Mobile Development)" — which ran into the
            # right-aligned link and overlapped it. The technologies belong in the
            # bullets, where they are doing work, not in a heading.
            title = entry.get("name", "")
            link = entry.get("link")
            out.append(f"### {title} @@ {link}" if link else f"### {title}")
            for bullet in entry.get("bullets") or []:
                out.append(f"- {bullet}")
            out.append("")

    if resume.get("certificates"):
        out += ["## Certificates and Achievements", ""]
        for entry in resume["certificates"]:
            name_and_date = entry.get("name", "")
            if entry.get("date"):
                name_and_date += f" — {entry['date']}"
            link = entry.get("link")
            out.append(f"- {name_and_date} @@ {link}" if link
                       else f"- {name_and_date}")
        out.append("")

    if resume.get("volunteer"):
        out += ["## Volunteer and Community Involvement", ""]
        for entry in resume["volunteer"]:
            # The organisation, and nothing else.
            #
            # It used to read "Al-Azhar Garden Student's Association / STEM
            # Co-Lead" — a slash doing the work of a sentence. The role belongs in
            # the description, where it can be a clause rather than a label:
            # "As STEM Co-Lead, organised STEM-focused events including..."
            #
            # If a model returns the role separately anyway, it still gets in — as
            # the opening of the sentence, not as a heading.
            out.append(f"### {entry.get('organization', '')}")

            description = str(entry.get("description") or "").strip()
            role = str(entry.get("role") or "").strip()
            if role and description and role.lower() not in description.lower():
                description = f"As {role}, {description[0].lower()}{description[1:]}"

            if description:
                out.append(description)
            out.append("")

    return "\n".join(out).rstrip() + "\n"
