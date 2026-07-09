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


def load_profile() -> dict:
    """Return the candidate profile the scorer matches jobs against."""
    return load_yaml(PROFILE_FILE)
