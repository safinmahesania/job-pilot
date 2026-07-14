"""Which jobs should be in your feed, and which should never have got there.

There are two judgements in this pipeline and they have never spoken to each other:

    rerank.py  gives every job a SCORE, and the feed shows it at 70 or above.
    resume_fit gives every job an OVERLAP, and refuses a resume below 15%.

A sales job once scored high enough to reach the feed AND high enough for you to
click "tailor resume", and only then did the fit check catch it — at which point the
question was not "why did the resume fail" but "why is a Canada Sales Talent
Community posting in a computer science student's feed at all".

This script puts both numbers next to each other for every job in the database and
sorts by how badly they disagree.

    python -m scripts.audit_jobs                 # everything in the feed
    python -m scripts.audit_jobs --all           # every job, scored or not
    python -m scripts.audit_jobs --llm           # ask the model for requirements
                                                 # (slow, one call per job — the
                                                 # default reads the title and
                                                 # description, which is what
                                                 # resume_fit falls back to anyway)
    python -m scripts.audit_jobs --csv audit.csv # write it out

The four verdicts:

    OK          both agree it belongs. Nothing to see.
    LEAK        the feed let it through and the fit check would refuse it.
                THIS IS THE BUG. A job you can see and cannot honestly apply to.
    MISSED      the fit check says you are a match and the feed hid it.
                A job you should have seen.
    AGREED-NO   both say no. Working as intended.
"""
import argparse
import csv
import sqlite3
import sys

from src import resume_fit
from src.config import load_profile
from src.paths import (DB_PATH, DEFAULT_SCORE_THRESHOLD,
                       FIT_MIN_OVERLAP, FIT_MIN_TECHNOLOGIES)


def _requirements(job: dict, use_llm: bool) -> list[str]:
    """What the job asks for.

    Without --llm this reads the title and the description, which is exactly what
    resume_fit falls back to when the model fails — so the audit reflects the same
    judgement the live path would reach on a bad day, which is the one worth
    auditing.
    """
    if use_llm:
        from src.apply import extract_requirements
        try:
            found = extract_requirements(job)
            if found:
                return found
        except Exception as e:
            print(f"    (model failed on '{job.get('title')}': {e})",
                  file=sys.stderr)

    # Return nothing, and let resume_fit fall back exactly as it does live.
    #
    # The first version of this handed the whole 2000-character description over as
    # if it were a requirement list, and the numbers that came back were garbage —
    # a TD Bank software engineering internship read as 13% and got reported as a
    # LEAK. It was not a leak. It was this function measuring the wrong thing and
    # then blaming rerank.py for the answer.
    #
    # An audit that does not run the code it is auditing is not an audit.
    return []


def audit(conn, profile: dict, *, use_llm: bool, everything: bool) -> list[dict]:
    threshold = DEFAULT_SCORE_THRESHOLD

    sql = "SELECT id, title, company, score, description FROM jobs"
    if not everything:
        sql += " WHERE score IS NOT NULL"
    sql += " ORDER BY score DESC NULLS LAST"

    rows = []
    for row in conn.execute(sql):
        job = dict(row)
        score = job.get("score")

        requirements = _requirements(job, use_llm)

        # The DECISION is the count, not the ratio — see check_fit. The ratio is
        # printed alongside it because it is what the old audit reported, and seeing
        # the two together is the clearest possible statement of why it was wrong.
        wanted = resume_fit.technologies_wanted(job, profile)
        languages = resume_fit.languages_wanted(job, profile)
        overlap, _ = resume_fit.overlap(requirements, profile, job)

        # A verdict on a posting nobody fetched is not a verdict.
        #
        # An Achievers "Data Scientist" role came back naming none of this profile's
        # technologies — no Python, no PyTorch, no scikit-learn, no Pandas. For a
        # data science posting that is not a low score, it is a missing description:
        # the scraper stored a title and a stub, and the fit check dutifully read
        # nothing and reported nothing found.
        #
        # The verdict is unchanged. The row just says so.
        described = len(str(job.get("description") or "").strip()) >= 200

        in_feed = score is not None and score >= threshold
        # The same decision check_fit makes. An audit that reaches a different
        # verdict from the code it audits is not auditing anything.
        fits = bool(languages) or len(wanted) >= FIT_MIN_TECHNOLOGIES
        matched = wanted

        if in_feed and not fits:
            verdict = "LEAK"
        elif not in_feed and fits and score is not None:
            verdict = "MISSED"
        elif in_feed and fits:
            verdict = "OK"
        else:
            verdict = "AGREED-NO"

        rows.append({
            "id": job["id"],
            "title": (job.get("title") or "")[:44],
            "company": (job.get("company") or "")[:20],
            "feed_score": round(score) if score is not None else None,
            "tech": len(wanted),
            "langs": ", ".join(sorted(languages)) or "-",
            "described": described,
            "fit": round(overlap * 100),
            "verdict": verdict,
            "matched": ", ".join(sorted(matched)[:6]),
        })

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true",
                        help="every job, including unscored ones")
    parser.add_argument("--llm", action="store_true",
                        help="ask the model what each job requires (slow)")
    parser.add_argument("--csv", metavar="FILE",
                        help="write the table to a CSV you can hand to someone")
    args = parser.parse_args()

    profile = load_profile()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = audit(conn, profile, use_llm=args.llm, everything=args.all)
    conn.close()

    if not rows:
        print("No jobs in the database yet. Run a fetch first.")
        return

    # The disagreements first. They are the only rows anyone needs to read.
    order = {"LEAK": 0, "MISSED": 1, "OK": 2, "AGREED-NO": 3}
    rows.sort(key=lambda r: (order[r["verdict"]], -(r["feed_score"] or 0)))

    print()
    print(f"{'ID':>4}  {'VERDICT':<10} {'FEED':>4} {'TECH':>4}  {'YOUR LANGUAGES':<22} "
          f"{'TITLE':<40} COMPANY")
    print("-" * 126)
    for r in rows:
        feed = r["feed_score"] if r["feed_score"] is not None else "--"
        stub = "" if r["described"] else "  << no description stored"
        print(f"{r['id']:>4}  {r['verdict']:<10} {feed:>4} {r['tech']:>4}  "
              f"{r['langs'][:22]:<22} {r['title'][:40]:<40} {r['company']}{stub}")

    thin = [r for r in rows if not r["described"]]
    if thin:
        print()
        print(f"  {len(thin)} job(s) have no description stored. The fit check read "
              f"nothing for them,")
        print("  so their verdict says nothing either. That is a fetching problem, "
              "not a scoring one.")

    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    print()
    print(f"  {counts.get('OK', 0):>3}  OK          the feed and the fit check agree")
    print(f"  {counts.get('LEAK', 0):>3}  LEAK        in your feed, cannot honestly "
          f"be applied to  <-- the bug")
    print(f"  {counts.get('MISSED', 0):>3}  MISSED      a real match the feed hid "
          f"from you")
    print(f"  {counts.get('AGREED-NO', 0):>3}  AGREED-NO   both say no")
    print()
    print(f"  feed threshold: {DEFAULT_SCORE_THRESHOLD}   "
          f"fit: any language you write, or {FIT_MIN_TECHNOLOGIES}+ of your tools")

    if counts.get("LEAK"):
        print()
        print("  The LEAK rows are jobs rerank.py scored highly and resume_fit would")
        print("  refuse. One of the two is wrong about you. Read them and see which.")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\n  written to {args.csv}")


if __name__ == "__main__":
    main()
