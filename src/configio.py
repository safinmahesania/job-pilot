"""Read and write the YAML config files from the frontend.

A timestamp-free ``.bak`` copy is written to the backups directory before every
save, so a bad edit from the UI can always be recovered.

Caveat: ``yaml.safe_dump`` does not preserve comments. The first time the
frontend writes ``companies.yaml``, the section headers and inline notes in that
file are lost — the data survives, the commentary does not. Keep the annotated
copy in git.
"""
from pathlib import Path
import shutil
import yaml

from src.paths import CONFIG_DIR, BACKUP_DIR


def _path(name: str) -> Path:
    """Resolve a config file name safely inside CONFIG_DIR.

    Guards against path traversal and non-YAML targets: the resolved file must
    sit directly in the config directory and end in .yaml/.yml.
    """
    p = (CONFIG_DIR / name).resolve()
    if p.parent != CONFIG_DIR or not p.name.endswith((".yaml", ".yml")):
        raise ValueError(f"refusing to touch {name}")
    return p


def _backup(p: Path):
    """Copy the current file to backups/<name>.bak before it is overwritten."""
    if p.exists():
        BACKUP_DIR.mkdir(exist_ok=True)
        shutil.copy2(p, BACKUP_DIR / (p.name + ".bak"))


def read_yaml(name: str):
    """Parse a config file and return the data structure."""
    with open(_path(name), encoding="utf-8") as f:
        return yaml.safe_load(f)


def write_yaml(name: str, data):
    """Serialise `data` to a config file, backing up the previous version."""
    p = _path(name)
    _backup(p)
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True,
                       default_flow_style=False)


def read_text(name: str) -> str:
    """Return the raw text of a config file (for the raw-YAML editor)."""
    return _path(name).read_text(encoding="utf-8")


def write_text(name: str, text: str):
    """Validate then write raw YAML text, backing up the previous version.

    The text is parsed first so invalid YAML is rejected before it can
    overwrite a working config.
    """
    yaml.safe_load(text)          # raises if invalid — never write broken YAML
    p = _path(name)
    _backup(p)
    p.write_text(text, encoding="utf-8")
