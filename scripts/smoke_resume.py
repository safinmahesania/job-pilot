"""Run the real resume path against a real job, and read what comes out.

Four hundred and forty-five tests pass. Not one of them has ever called the live
model, loaded your real profile.yaml, or opened the file that lands in your
downloads folder. The whole selection rewrite — the model returns indices, the code
assembles the page from the profile — has been proven in a sandbox and never once
run.

A green suite is not a working feature. This is the difference, and it is the gap
that has stayed open longest.

    python -m scripts.smoke_resume              # your highest-scoring real job
    python -m scripts.smoke_resume --job 59     # a specific one
    python -m scripts.smoke_resume --keep       # leave the .docx and .pdf behind

It calls apply.generate_resume() — the real one, with the real provider chain —
renders the .docx and the .pdf through the same code the download button uses, and
then reads both files back and checks them. Everything the last dozen bugs would
have shown up as, it looks for by name.

It is not a unit test. It is the thing a unit test cannot do: prove the pieces work
together, on your machine, with your data, against a model that does not know it is
being watched.
"""
import argparse
import io
import re
import sqlite3
import sys
import zipfile
from pathlib import Path

from src import apply, materials, resume_guard
from src.config import load_profile
from src.paths import DB_PATH

PASS, FAIL, WARN = "pass", "fail", "warn"


class Report:
    def __init__(self):
        self.rows = []

    def ok(self, what, detail=""):
        self.rows.append((PASS, what, detail))

    def bad(self, what, detail=""):
        self.rows.append((FAIL, what, detail))

    def warn(self, what, detail=""):
        self.rows.append((WARN, what, detail))

    def print(self, title):
        print(f"\n  {title}")
        print("  " + "-" * 76)
        for verdict, what, detail in self.rows:
            mark = {PASS: "  ok  ", FAIL: " FAIL ", WARN: " warn "}[verdict]
            print(f"  [{mark}] {what}")
            if detail:
                print(f"           {detail}")

    @property
    def failed(self):
        return sum(1 for v, _, _ in self.rows if v == FAIL)


def pick_job(conn, job_id):
    """A real job from your database, with enough text to be worth reading."""
    if job_id:
        row = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            sys.exit(f"No job {job_id} in the database.")
        return dict(row)

    row = conn.execute(
        "SELECT * FROM jobs WHERE length(description) > 400 "
        "AND score IS NOT NULL ORDER BY score DESC LIMIT 1").fetchone()
    if not row:
        sys.exit("No job with a real description in the database. Run a fetch.")
    return dict(row)


def check_markdown(text, profile, report):
    """The page itself, before any renderer touches it."""
    lowered = text.lower()

    for marker in ("{{", "}}", "<!--", "todo", "tbd", "lorem ipsum", "your name",
                   "[insert", "xxx"):
        if marker in lowered:
            report.bad("no placeholders reached the page", f"found {marker!r}")
            break
    else:
        report.ok("no placeholders reached the page")

    # EVERY job and EVERY school. The model returns indices; an index it forgets must
    # not be able to delete a job from your history.
    missing = [e.get("company") for e in (profile.get("experience") or [])
               if e.get("company") and e["company"].lower() not in lowered]
    if missing:
        report.bad("every job you have held is on the page", f"missing: {missing}")
    else:
        report.ok("every job you have held is on the page")

    missing = [e.get("institution") for e in (profile.get("education") or [])
               if e.get("institution") and e["institution"].lower() not in lowered]
    if missing:
        report.bad("every school is on the page", f"missing: {missing}")
    else:
        report.ok("every school is on the page")

    # The summary is the ONE thing the model writes. It is the only place a lie can
    # still enter, which is why it is the only place still guarded.
    match = re.search(r"##\s*Summary\s*\n+(.+?)(?=\n##|\Z)", text, re.S | re.I)
    summary = match.group(1).strip() if match else ""

    if not summary:
        report.bad("the summary is not empty")
    else:
        report.ok("the summary is not empty", f"{len(summary)} chars")

        name = str((profile.get("identity") or {}).get("name", "")).strip()
        first = name.split()[0] if name else ""
        if first and first.lower() in summary.lower():
            report.bad("the summary does not name you", summary[:60])
        else:
            report.ok("the summary does not name you")

        claims = resume_guard.check_prose({"summary": summary}, profile)
        if claims:
            report.bad("every technology in the summary is yours", claims[0][:64])
        else:
            report.ok("every technology in the summary is yours")

        # Only the bullets under Experience and Projects, because only those are
        # highlights. Skills, education and certificates render as bullets too — the
        # first version compared "Microsoft Certified - Azure Fundamentals — Jun 2023"
        # against the highlight list, did not find it, and called a real certificate a
        # fabrication. It was reading the page without knowing what part it was reading.
        yours = set()
        for entry in profile.get("experience") or []:
            yours.update(str(h).strip() for h in (entry.get("highlights") or []))
        for entry in profile.get("projects") or []:
            yours.update(str(h).strip() for h in (entry.get("highlights") or []))

        checked, invented = [], []
        section = ""
        for line in text.splitlines():
            # (?!#) — "### Flutter Developer" is a job title, not a section. Matching it
            # here reset `section` to the job's own name, so nothing was ever under Work
            # Experience and the check examined ZERO bullets. A check that looks at
            # nothing passes.
            heading = re.match(r"##(?!#)\s*(.+)", line)
            if heading:
                section = heading.group(1).strip().lower()
                continue
            if not line.startswith("- "):
                continue
            if not any(word in section for word in ("experience", "project")):
                continue

            bullet = re.sub(r"\*\*|\*|__|_", "", line[2:]).strip()
            checked.append(bullet)
            if bullet not in yours:
                invented.append(bullet)

        if invented:
            report.bad("every bullet is yours, word for word",
                       f"{len(invented)} not in profile: {invented[0][:52]}")
        elif not checked:
            report.warn("no experience or project bullets on the page to check")
        else:
            report.ok("every bullet is yours, word for word", f"{len(checked)} checked")


def check_docx(data, report):
    """The .docx, read as XML. What Word will actually show."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8")
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")

    fonts = set(re.findall(r'w:ascii="([^"]+)"', xml))
    if fonts and fonts <= {"Calibri"}:
        report.ok("Calibri everywhere")
    else:
        report.bad("Calibri everywhere", f"found {sorted(fonts)}")

    sizes = sorted({int(s) // 2 for s in re.findall(r'<w:sz w:val="(\d+)"', xml)})
    if sizes == [11, 20]:
        report.ok("name 20pt, everything else 11pt")
    else:
        report.bad("name 20pt, everything else 11pt", f"found {sizes}pt")

    paragraphs = re.findall(r"<w:p>.*?</w:p>", xml, re.S)
    contact = next((p for p in paragraphs if "@" in p), "")
    contact_text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", contact))

    if contact_text.count("|") >= 2:
        report.ok("contact on one line", contact_text[:58])
    else:
        report.bad("contact on one line", contact_text[:58] or "(not found)")

    if "<w:jc w:val=\"both\"/>" in contact:
        report.bad("the contact line is not justified",
                   "justification stretches the phone number")
    else:
        report.ok("the contact line is not justified")

    links = re.findall(r'Target="(https?://[^"]+)"', rels)
    if links:
        report.ok("links are clickable", f"{len(links)} found")
    else:
        report.bad("links are clickable", "no hyperlink relationships")

    body = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))
    if "https://" in body or "http://" in body:
        report.bad("no raw URL is printed on the page")
    else:
        report.ok("no raw URL is printed on the page")

    if "skill category" in body.lower() or "owner:" in body.lower():
        report.bad("no annotation leaked onto the page")
    else:
        report.ok("no annotation leaked onto the page")

    for marker in ("**", "##", "- ["):
        if marker in body:
            report.bad("no markdown markers on the page", f"found {marker!r}")
            break
    else:
        report.ok("no markdown markers on the page")

    if "ListBullet" in xml:
        report.ok("bullets are real bullets")
    else:
        report.warn("bullets are real bullets", "no ListBullet style found")


def check_pdf(data, report):
    """The .pdf, read back with pypdf. The renderer nobody was watching."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    text = "\n".join(p.extract_text(extraction_mode="layout") or ""
                     for p in reader.pages)

    if re.search(rb"(Calibri|Carlito)", data):
        report.ok("Calibri or Carlito in the PDF")
    else:
        report.bad("Calibri or Carlito in the PDF", "neither font is embedded")

    if re.search(rb"Times", data):
        report.bad("no Times in the PDF")
    else:
        report.ok("no Times in the PDF")

    if "\u2022" in text:
        report.ok("real bullet characters, not hyphens")
    elif re.search(r"^\s*-\s", text, re.M):
        report.bad("real bullet characters, not hyphens", "found hyphens")
    else:
        report.warn("real bullet characters, not hyphens", "no bullets found")

    urls = []
    for page in reader.pages:
        for annotation in page.get("/Annots") or []:
            action = annotation.get_object().get("/A") or {}
            if action.get("/URI"):
                urls.append(str(action["/URI"]))

    if urls:
        report.ok("links are clickable in the PDF", f"{len(urls)} found")
    else:
        report.bad("links are clickable in the PDF", "no /URI annotations")

    if "https://" in text or "http://" in text:
        report.bad("no raw URL printed in the PDF")
    else:
        report.ok("no raw URL printed in the PDF")

    contact = next((l for l in text.splitlines() if "@" in l), "")
    if contact.count("|") >= 2:
        report.ok("contact on one line in the PDF", contact.strip()[:58])
    else:
        report.bad("contact on one line in the PDF",
                   contact.strip()[:58] or "(not found)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=int, help="a specific job id")
    parser.add_argument("--keep", action="store_true",
                        help="leave the .docx and .pdf behind so you can open them")
    args = parser.parse_args()

    profile = load_profile()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    job = pick_job(conn, args.job)
    conn.close()

    print(f"\n  JOB {job['id']}: {job['title']}")
    print(f"  {job.get('company')}  ·  feed score {job.get('score')}")
    print(f"\n  Calling the real generate_resume(). This hits the live provider "
          f"chain.")

    try:
        result = apply.generate_resume(job)
    except resume_guard.FabricationError as e:
        print(f"\n  REFUSED — the model could not produce an honest resume.")
        print(f"  {e}")
        print(f"\n  This is the guard working. It is not a crash. But if it happens "
              f"on\n  a job that plainly fits you, the reason is worth reading.")
        sys.exit(1)
    except Exception as e:
        print(f"\n  CRASHED: {type(e).__name__}: {e}")
        raise

    text = result["text"]
    print(f"  provider: {result['provider']}   "
          f"projects used: {', '.join(result['projects_used']) or 'none'}")

    if result["overruns"]:
        print(f"  overruns: {result['overruns']}")

    report = Report()
    check_markdown(text, profile, report)

    docx = materials.to_docx(text, "resume")
    check_docx(docx, report)

    pdf = materials.to_pdf(text, "resume")
    check_pdf(pdf, report)

    report.print(f"THE PAGE, THE WORD FILE, AND THE PDF")

    if args.keep:
        out = Path("smoke")
        out.mkdir(exist_ok=True)
        (out / "resume.md").write_text(text, encoding="utf-8")
        (out / "resume.docx").write_bytes(docx)
        (out / "resume.pdf").write_bytes(pdf)
        print(f"\n  written to {out.resolve()}  — open them and look")

    print()
    if report.failed:
        print(f"  {report.failed} FAILED. The suite is green and the feature is "
              f"not.")
        sys.exit(1)

    print(f"  All {len(report.rows)} checks pass, against the live model, your real "
          f"profile,\n  and the actual files the download button produces.")
    print(f"\n  Run with --keep and open them. A test cannot tell you it looks "
          f"right.")


if __name__ == "__main__":
    main()
