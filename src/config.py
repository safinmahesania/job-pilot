"""Load YAML config (companies, profile)."""
import yaml


def load_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_companies() -> list[dict]:
    return load_yaml("companies.yaml")["companies"]


def load_profile() -> dict:
    return load_yaml("profile.yaml")