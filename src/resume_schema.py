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


#: What the model is asked to return. Every key required; empty lists allowed only
#: where the profile is genuinely empty.
SHAPE = {
    "summary": "string — 2-3 sentences, angled at this job",
    "skills": [{"label": "string — copied EXACTLY from my profile",
                "skills": ["string", "..."]}],
    "experience": [{"role": "string", "company": "string",
                    "location": "string", "dates": "string — e.g. May 2024 - Aug 2024",
                    "bullets": ["string", "..."]}],
    "education": [{"degree": "string", "institution": "string",
                   "location": "string", "dates": "string"}],
    "projects": [{"name": "string", "owner": "string", "tech": ["string"],
                  "link": "string", "bullets": ["string", "..."]}],
    "certificates": [{"name": "string", "date": "string", "link": "string"}],
    "volunteer": [{"organization": "string", "role": "string",
                   "description": "string"}],
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


def _contact_line(profile: dict, redacted: bool) -> list[str]:
    if redacted:
        return ["{{LOCATION}}", "{{EMAIL}} | {{PHONE}} | {{LINKS}}"]

    contact = profile.get("contact") or {}
    where = ", ".join(
        part for part in (contact.get("city"), contact.get("province"),
                          contact.get("country")) if part
    )
    rest = [contact.get("email"), contact.get("phone"),
            contact.get("linkedin"), contact.get("github")]
    return [where, " | ".join(p for p in rest if p)]


def to_markdown(resume: dict, profile: dict, name: str,
                redacted: bool = False) -> str:
    """Render the structured resume to the markdown the docx and PDF renderers eat.

    This is the ONLY place the document's shape is decided. The model never sees a
    `###` or an `@@` — it cannot get them wrong, because it is never asked for them.
    """
    out = [f"# {name}", ""]
    out.extend(_contact_line(profile, redacted))
    out.append("")

    if resume.get("summary"):
        out += ["## Summary", "", resume["summary"], ""]

    if resume.get("skills"):
        out += ["## Skills", ""]
        for group in resume["skills"]:
            skills = " | ".join(str(s) for s in (group.get("skills") or []))
            if group.get("label") and skills:
                out.append(f"- **{group['label']}:** {skills}")
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
            title = entry.get("name", "")
            if entry.get("owner"):
                title += f" - {str(entry['owner']).title()}"
            tech = ", ".join(str(t) for t in (entry.get("tech") or []))
            if tech:
                title += f" ({tech})"
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
            heading = " / ".join(p for p in (entry.get("organization"),
                                             entry.get("role")) if p)
            out.append(f"### {heading}")
            if entry.get("description"):
                out.append(entry["description"])
            out.append("")

    return "\n".join(out).rstrip() + "\n"
