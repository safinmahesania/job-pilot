"""Why was this job refused? — the fit gate, shown step by step.

The refusal says "you can't honestly apply to this one" and lists what matched. When
that list is one odd word — "github" — the interesting question is which side is
wrong: the posting genuinely names nothing you have, or your profile isn't being
read the way you think it is.

The second failure is silent and easy to miss. Skills listed under a category label
the gate doesn't count as evidence are dropped without a word, and a profile full of
languages can arrive at the gate nearly empty.

    python -m scripts.fit_doctor <job_id>
    python -m scripts.fit_doctor            # lists recent jobs to pick from

Prints counts and category labels, never the skills themselves, so the output is
safe to paste.
"""
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.modules.setdefault("ollama", types.ModuleType("ollama"))

from src import configio, store                              # noqa: E402
from src.paths import FIT_MIN_TECHNOLOGIES                    # noqa: E402
from src.resume_fit import (                                  # noqa: E402
    _LANGUAGES, _is_evidence, my_fields, my_technologies, technologies_wanted,
)


def _rule(title=""):
    print(f"\n{'─' * 66}")
    if title:
        print(title)


def list_jobs(conn):
    rows = conn.execute(
        "SELECT id, title, company FROM jobs ORDER BY id DESC LIMIT 15").fetchall()
    print("Recent jobs — run again with one of these ids:\n")
    for r in rows:
        print(f"  {r['id']:>5}  {(r['title'] or '')[:44]:44}  {(r['company'] or '')[:20]}")


def main():
    conn = store.connect()
    conn.row_factory = __import__("sqlite3").Row

    if len(sys.argv) < 2:
        list_jobs(conn)
        return

    job_id = sys.argv[1]
    row = conn.execute(
        "SELECT id, title, company, description FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    if not row:
        print(f"No job with id {job_id}.")
        return
    job = dict(row)

    profile = configio.read_yaml("profile.yaml") or {}
    if not profile:
        print("No profile.yaml could be read — that alone would explain the refusal.")
        return

    # ── 1. What the gate loaded from your profile ────────────────────────────
    _rule("1. YOUR PROFILE, as the gate reads it")

    skills = profile.get("skills") or {}
    tiered = sum(len(skills.get(t) or []) for t in ("expert", "proficient", "familiar"))
    print(f"   skills.expert/proficient/familiar : {tiered} entries")

    groups = profile.get("skill_categories") or []
    kept_groups, dropped_groups, kept_n, dropped_n = [], [], 0, 0
    for g in groups:
        if not isinstance(g, dict):
            continue
        label = str(g.get("label", ""))
        n = len(g.get("skills") or [])
        if _is_evidence(label):
            kept_groups.append((label, n))
            kept_n += n
        else:
            dropped_groups.append((label, n))
            dropped_n += n

    print(f"   skill_categories                  : {len(groups)} groups")
    for label, n in kept_groups:
        print(f"        counted   {n:>3} skills   \"{label}\"")
    for label, n in dropped_groups:
        print(f"        DROPPED   {n:>3} skills   \"{label}\"   <- not treated as evidence")

    tech = my_technologies(profile)
    fields = my_fields(profile)
    print(f"\n   -> technologies the gate has : {len(tech)}")
    print(f"   -> of those, languages       : {len(tech & _LANGUAGES)}")
    print(f"   -> fields                    : {len(fields)}")

    if dropped_n and len(tech & _LANGUAGES) == 0:
        print("\n   NOTE: you have no languages loaded AND a category was dropped.")
        print("         If your languages live in that category, that is the bug —")
        print("         rename it to something the gate counts (e.g. \"Programming")
        print("         Languages\") or move them into skills.expert.")

    # ── 2. What this posting names ───────────────────────────────────────────
    _rule("2. THIS POSTING")
    desc = job.get("description") or ""
    print(f"   {job['title']} — {job['company']}")
    print(f"   description: {len(desc)} chars"
          + ("   <- empty, so there is nothing to match against" if len(desc) < 50 else ""))

    wanted = technologies_wanted(job, profile)
    print(f"\n   of your {len(tech)} technologies, this posting names {len(wanted)}:")
    for t in sorted(wanted):
        tag = "  (a language)" if t in _LANGUAGES else ("  (a field)" if t in fields else "")
        print(f"        {t}{tag}")
    if not wanted:
        print("        (none)")

    # ── 3. The decision ──────────────────────────────────────────────────────
    _rule("3. THE DECISION")
    langs = wanted & _LANGUAGES
    flds = wanted & fields
    if langs:
        print(f"   PASS — names a language you write: {', '.join(sorted(langs))}")
    elif flds:
        print(f"   PASS — names a field of yours: {', '.join(sorted(flds))}")
    elif len(wanted) >= FIT_MIN_TECHNOLOGIES:
        print(f"   PASS — names {len(wanted)} of your tools (needs {FIT_MIN_TECHNOLOGIES})")
    else:
        print("   REFUSED — no language of yours, no field of yours, and only")
        print(f"             {len(wanted)} tool (needs {FIT_MIN_TECHNOLOGIES}).")
        print()
        if len(tech) < 8:
            print("   Most likely cause: the gate is only holding "
                  f"{len(tech)} technologies from your")
            print("   profile. That is few enough that real matches would be missed.")
        elif len(desc) < 400:
            print("   Most likely cause: this posting has almost no description to")
            print("   read — imported from an alert email that couldn't be expanded.")
        else:
            print("   The posting genuinely names nothing you listed. If you do have")
            print("   what it asks for, it is missing from profile.yaml — add it.")
    print()


if __name__ == "__main__":
    main()
