"""Show exactly what the model is sent when it writes your resume.

When a resume comes back with an employer you never worked for, there are only
three possible explanations, and guessing between them wastes an afternoon:

  1. The profile is not being read — a wrong path, a parse error, a key the code
     does not look for. The model was handed nothing and invented from the job.
  2. The profile IS being read, and the model ignored it. That is a model problem,
     not a data problem, and the fix is a different model.
  3. The profile is read but a section of it never reaches the prompt — a field
     name that does not match, or a loop sitting after a `return`.

You cannot tell these apart from the output. You can tell them apart instantly by
looking at the prompt. So look at it:

    python scripts/debug_prompt.py

It prints the profile as loaded, the fact sheet the model actually receives, and
flags anything in your profile that is not making it through.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_profile
from src import apply
from src.paths import CONFIG_DIR, PROFILE_FILE

# The guard is a separate delivery. This script's whole job is to tell you what
# is wrong, so it must run even when part of the app is missing — a diagnostic
# that needs the thing you're diagnosing is not a diagnostic.
try:
    from src import resume_guard
except ImportError:
    resume_guard = None


def rule(title):
    print(f"\n{'═' * 74}\n  {title}\n{'═' * 74}")


def main():
    path = CONFIG_DIR / PROFILE_FILE
    rule("1. IS THE PROFILE BEING READ AT ALL?")
    print(f"  reading: {path}")
    print(f"  exists:  {path.exists()}")
    if not path.exists():
        print("\n  🔴 THERE IS NO PROFILE. This alone explains a fabricated resume:")
        print("     the model is handed an empty fact sheet and a job description,")
        print("     and the job description is the only thing it can write from.")
        return

    profile = load_profile()
    if not profile:
        print("\n  🔴 The file parsed to nothing. Check the YAML.")
        return
    print(f"  parsed:  {len(profile)} top-level keys")

    rule("2. WHAT IS ACTUALLY IN IT?")
    sections = {
        "identity.name": (profile.get("identity") or {}).get("name"),
        "summary": (profile.get("summary") or "").strip()[:40],
        "skill_categories": len(apply.skill_groups(profile)),
        "experience": len(profile.get("experience") or []),
        "projects": len(profile.get("projects") or []),
        "education": len(profile.get("education") or []),
        "certificates": len(profile.get("certificates") or []),
        "volunteer": len(profile.get("volunteer") or []),
    }
    for key, value in sections.items():
        empty = value in (0, None, "", [])
        print(f"  {'🔴' if empty else '  '} {key:18} {value!r}")

    rule("3. WOULD GENERATION EVEN BE ALLOWED?")
    if resume_guard is None:
        print("  (src/resume_guard.py is not installed — skipping. Copy it in:")
        print("   it is what refuses to generate from a profile too thin to")
        print("   support a resume, and refuses to return an invented one.)")
        missing = []
    else:
        missing = resume_guard.validate_profile(profile)
    if missing:
        print("  🔴 REFUSED. The resume would not be generated:")
        for m in missing:
            print(f"     - {m}")
    else:
        print("  OK — the profile can support a resume.")

    rule("4. THE FACT SHEET THE MODEL RECEIVES")
    print("  This is the ONLY source of truth the model is given about you.")
    print("  If a section is missing here, it cannot appear on your resume —")
    print("  and the model will fill the gap from the job description.\n")

    facts = apply._profile_facts(profile)
    if not facts.strip():
        print("  🔴 EMPTY. The model is told nothing about you.")
    else:
        for line in facts.splitlines():
            print(f"    {line}")

    rule("5. ANYTHING IN YOUR PROFILE THAT NEVER REACHES THE MODEL")
    checks = [
        ("experience", "Experience:", profile.get("experience")),
        ("education", "Education:", profile.get("education")),
        ("certificates", "Certificate:", profile.get("certificates")),
        ("volunteer", "Volunteer:", profile.get("volunteer")),
        ("skill_categories", "Skill category", profile.get("skill_categories")),
    ]
    dropped = []
    for name, marker, value in checks:
        if value and marker not in facts:
            dropped.append(name)
            print(f"  🔴 {name}: filled in profile.yaml, but NOT in the prompt.")
    if not dropped:
        print("  Everything in your profile reaches the model.")

    rule("6. PROJECTS AS THE MODEL SEES THEM")
    rendered = apply._format_projects(profile.get("projects") or [])
    print(rendered[:1500] + ("\n    ..." if len(rendered) > 1500 else ""))

    # The tell for a string where a list belongs: bullets one character long.
    letters = sum(1 for line in rendered.splitlines()
                  if line.strip().startswith("- ") and len(line.strip()) <= 4)
    if letters > 5:
        print(f"\n  🔴 {letters} single-character bullets.")
        print("     A `highlights:` in your profile is a STRING, not a list, and")
        print("     Python is iterating it one letter at a time. Write it as:")
        print("       highlights:")
        print("         - your first point")
        print("         - your second point")

    rule("7. WHICH MODEL WILL WRITE IT?")
    from src import llm
    try:
        print(f"  privacy mode: {llm.privacy_mode()}")
    except Exception as e:
        print(f"  privacy mode: could not read ({e})")
    try:
        print("  provider order:", " -> ".join(llm.get_order()))
    except Exception as e:
        print(f"  provider order: could not read ({e})")
    print("\n  A small local model will ignore instructions a hosted one obeys.")
    print("  If the fact sheet above is complete and the resume was still invented,")
    print("  the problem is the model, not the data.")


if __name__ == "__main__":
    main()
