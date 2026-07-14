"""Load the user's YAML configuration (companies + profile).

Thin read-only helpers used by the pipeline. All file locations come from
`src.paths`, so this module never hard-codes a directory.
"""
import yaml

from src.paths import CONFIG_DIR, COMPANIES_FILE, PROFILE_FILE


def load_yaml(name: str):
    """Read and parse a YAML file from the config directory."""
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_companies() -> list:
    """Return the list of company/board definitions to fetch from."""
    return load_yaml(COMPANIES_FILE)["companies"]


# Fields that must be lists. YAML will happily give you a string where you meant a
# list, and Python will happily iterate that string — one character at a time.
#
# This is not hypothetical. A profile with
#
#     highlights: Plant disease detection using MobileNetV3.
#
# instead of
#
#     highlights:
#       - Plant disease detection using MobileNetV3.
#
# produced a prompt containing several thousand bullets reading "- P", "- l",
# "- a", "- n", "- t", and nothing anywhere failed. The model was handed a wall of
# noise where the projects should have been, and the resume it wrote was
# correspondingly untethered.
#
# Normalising here rather than at each use site is deliberate: there were six
# places iterating these fields — in the resume writer, the cover letter, the
# scorer and the autofill — and a fix that has to be remembered six times is a fix
# that will be forgotten once.
_LIST_FIELDS = {
    "experience": ["highlights"],
    "projects": ["highlights", "tech"],
    "education": [],
}


def _as_list(value) -> list:
    """A string is one item, not a sequence of characters."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def normalise_profile(profile: dict) -> tuple[dict, list[str]]:
    """Coerce the fields that must be lists, and say what had to be fixed.

    The warnings matter as much as the coercion. Silently accepting a malformed
    profile means you never learn it is malformed, and the next field you get
    wrong will fail somewhere else, just as quietly.
    """
    warnings = []

    for section, fields in _LIST_FIELDS.items():
        entries = profile.get(section)
        if not isinstance(entries, list):
            continue
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or entry.get("role") or entry.get("degree") or f"#{i + 1}"
            for field in fields:
                if field not in entry:
                    continue
                if isinstance(entry[field], str):
                    warnings.append(
                        f"{section}[{name}].{field} is a single string. It should be "
                        f"a YAML list:\n"
                        f"      {field}:\n"
                        f"        - your first point\n"
                        f"        - your second point"
                    )
                entry[field] = _as_list(entry[field])

    # skills tiers
    skills = profile.get("skills")
    if isinstance(skills, dict):
        for tier in ("expert", "proficient", "familiar"):
            if isinstance(skills.get(tier), str):
                warnings.append(f"skills.{tier} is a string; it should be a list.")
            if tier in skills:
                skills[tier] = _as_list(skills[tier])

    # skill_categories entries
    categories = profile.get("skill_categories")
    if isinstance(categories, list):
        for entry in categories:
            if isinstance(entry, dict) and "skills" in entry:
                if isinstance(entry["skills"], str):
                    warnings.append(
                        f"skill_categories[{entry.get('label', '?')}].skills is a "
                        f"string; it should be a list."
                    )
                entry["skills"] = _as_list(entry["skills"])

    return profile, warnings


def load_profile() -> dict:
    """Return the candidate profile the scorer matches jobs against."""
    profile, warnings = normalise_profile(load_yaml(PROFILE_FILE) or {})
    for warning in warnings:
        print(f"[profile] {warning}")
    return profile
