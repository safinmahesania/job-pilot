"""Two judgements that have never spoken to each other.

    rerank.py   gives every job a SCORE, and the feed shows it at 70 or above.
    resume_fit  gives every job an OVERLAP, and refuses a resume below 15%.

A "Canada Sales - Talent Community" posting once scored high enough to reach a
computer science student's feed AND high enough for him to click "tailor resume" —
and only then did the fit check catch it. Which means the fit check worked and the
question was never "why did the resume fail". It was "why is this in his feed".

This script puts the two numbers side by side and names the disagreements. It is a
diagnostic, not a guard: it does not fix rerank.py, it shows you that rerank.py
needs fixing, which is the step that was missing.
"""
import sqlite3

import pytest

from scripts.audit_jobs import audit


PROFILE = {
    "identity": {"name": "Safin"},
    "summary": "Software developer across frontend and backend.",
    "skills": {
        "expert": ["Java", "Flutter", "Python", "SQL", "RESTful APIs", "Dart"],
        "proficient": ["C#", ".NET 8", "Firebase", "MVC Architecture"],
        "familiar": ["Azure", "PyTorch", "Apache Spark"],
    },
    "skill_categories": [{"label": "Databases", "skills": ["MySQL", "SQL Server"]}],
    "experience": [{"role": "Flutter Developer", "company": "Otrack",
                    "highlights": ["Built a Flutter app with REST APIs."]}],
    "projects": [{"name": "Recipedia", "tech": ["Flutter", "Dart", "Firebase"]}],
    "education": [{"degree": "MSc Computer Science", "institution": "Concordia"}],
    "certificates": [], "volunteer": [],
}

#: The real one, and the decoys that show the audit is not just saying "sales bad".
JOBS = [
    ("Canada Sales - Talent Community", "PointClickCare", 78,
     "Join our sales talent community. We seek Account Executives and Sales "
     "Development Representatives to drive revenue growth, manage pipeline in "
     "Salesforce, exceed quota, and build relationships with healthcare providers "
     "across Canada. What you'll do: prospect, qualify and close. Every day you "
     "will work with a collaborative team. Strong communication and time management "
     "are critical. Proficiency in MS Office. Fluent English required."),
    ("Junior Software Developer", "Shopify", 84,
     "Build and maintain backend services in Java and Python. REST APIs, SQL "
     "databases, code review, Agile delivery. What you'll do: ship code every day "
     "alongside senior engineers who will mentor you. We value curiosity and "
     "ownership. Requirements: a Computer Science degree or equivalent, familiarity "
     "with Git, and a willingness to learn quickly in a fast-paced environment."),
    ("Registered Nurse - ICU", "SickKids", 71,
     "Provide direct patient care in the intensive care unit. BScN required, current "
     "CNO registration, IV therapy certification, patient assessment and monitoring. "
     "What you'll do: care for critically ill children every day as part of a "
     "multidisciplinary team. Strong communication and attention to detail are "
     "essential in this demanding, fast-paced environment."),
    ("Backend Engineer (.NET)", "CGI", 68,
     "C#, .NET 8, SQL Server, MVC architecture, RESTful APIs, Azure. What you'll do: "
     "design and build enterprise backend services for our clients. Every day you "
     "will work in an Agile team with code review and pair programming. "
     "Requirements: strong object-oriented fundamentals and hands-on experience with "
     "relational databases and web services."),
    ("Financial Analyst", "RBC", 65,
     "Financial modelling, variance analysis, forecasting and budgeting. CPA "
     "candidate preferred. What you'll do: partner with business units to build "
     "budgets and report on performance every month. Advanced MS Excel required. "
     "Strong communication and attention to detail. Fluency in English. This is a "
     "critical role on a collaborative team."),
]


@pytest.fixture
def db(tmp_path):
    from pathlib import Path

    conn = sqlite3.connect(":memory:")
    schema = (Path(__file__).resolve().parent.parent
              / "data" / "schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)

    for i, (title, company, score, description) in enumerate(JOBS):
        conn.execute(
            "INSERT INTO jobs (dedupe_hash, source, title, company, description, "
            "score) VALUES (?, 'test', ?, ?, ?, ?)",
            (f"h{i}", title, company, description, score))
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def verdict_for(rows: list[dict], company: str) -> str:
    return next(r["verdict"] for r in rows if r["company"] == company)


class TestTheLeak:
    """Jobs the feed shows you that you cannot honestly apply to. The bug."""

    def test_the_sales_job_is_a_leak(self, db):
        """It scored 78 and it is 0% you. Both facts, at once, is the bug."""
        rows = audit(db, PROFILE, use_llm=False, everything=False)

        assert verdict_for(rows, "PointClickCare") == "LEAK"

    def test_a_nursing_job_is_a_leak_too(self, db):
        """It is not about sales. It is about rerank.py scoring jobs with nothing of
        yours in them."""
        rows = audit(db, PROFILE, use_llm=False, everything=False)

        assert verdict_for(rows, "SickKids") == "LEAK"

    def test_the_leak_reports_zero_overlap(self, db):
        rows = audit(db, PROFILE, use_llm=False, everything=False)
        sales = next(r for r in rows if r["company"] == "PointClickCare")

        assert sales["fit"] == 0
        assert sales["feed_score"] >= 70


class TestTheMissed:
    """The other direction, and the one nobody was looking for.

    CGI — C#, .NET 8, SQL Server, MVC, RESTful APIs, Azure, nearly every word of it
    in the profile — scored 68 and was invisible, because the feed showed 70 and
    above. It was the best match he had.

    The threshold was 70 because it was doing two jobs: ranking AND refusing. It had
    to, because the fit check underneath it could not be trusted. Now it can, so the
    feed can stop trying to be a gate, and this job is simply visible.
    """

    def test_the_best_match_you_have_is_now_visible(self, db):
        rows = audit(db, PROFILE, use_llm=False, everything=False)

        assert verdict_for(rows, "CGI") == "OK"

    def test_a_job_below_the_threshold_that_fits_is_still_reported(self, db):
        """The verdict has not stopped existing — the threshold moved past this
        particular job, and a lower one would still be flagged."""
        db.execute(
            "INSERT INTO jobs (dedupe_hash, source, title, company, description, "
            "score) VALUES ('low', 'test', 'Software Developer II', 'Workleap', "
            "'React, TypeScript, C#, .NET, Azure, SQL Server. Build and ship "
            "features. What you will do: work with AI-assisted tooling every day "
            "alongside a small team that owns its product end to end. Requirements: "
            "strong fundamentals and a bias for shipping.', 45)")
        db.commit()

        rows = audit(db, PROFILE, use_llm=False, everything=False)

        assert verdict_for(rows, "Workleap") == "MISSED"


class TestTheAgreements:
    def test_a_real_developer_job_is_ok(self, db):
        rows = audit(db, PROFILE, use_llm=False, everything=False)

        assert verdict_for(rows, "Shopify") == "OK"

    def test_the_price_of_a_generous_feed_is_paid_by_the_fit_check(self, db):
        """RBC's financial analyst role scored 65, so a threshold of 60 shows it —
        and the fit check refuses it, because it names no language he writes.

        That is the trade, stated plainly: rank generously, refuse strictly. A job
        he can see and cannot honestly apply to costs him one glance. A job he never
        sees costs him the job."""
        rows = audit(db, PROFILE, use_llm=False, everything=False)

        assert verdict_for(rows, "RBC") == "LEAK"

    def test_the_matched_terms_explain_the_verdict(self, db):
        """A number without a reason is not a diagnosis."""
        rows = audit(db, PROFILE, use_llm=False, everything=False)
        shopify = next(r for r in rows if r["company"] == "Shopify")

        assert shopify["matched"]
        assert "java" in shopify["matched"] or "python" in shopify["matched"]


class TestItDoesNotNeedTheModel:
    def test_the_default_reads_the_posting(self, db):
        """One LLM call per job would make this unusable on a hundred jobs — and the
        fallback is what resume_fit itself uses when the model fails, so the audit
        reflects the judgement the live path reaches on a bad day."""
        rows = audit(db, PROFILE, use_llm=False, everything=False)

        assert len(rows) == len(JOBS)
        assert all(r["verdict"] for r in rows)


class TestAJobNobodyFetched:
    """An Achievers "Data Scientist" role came back naming none of this profile's
    technologies — no Python, no PyTorch, no scikit-learn, no Pandas.

    For a data science posting that is not a low score. It is a missing description:
    the scraper stored a title and a stub, and the fit check dutifully read nothing
    and reported nothing found. The verdict was arithmetically correct and told you
    nothing at all.

    A verdict on a posting nobody fetched is not a verdict, and the row should say
    so rather than quietly looking like a judgement.
    """

    def test_a_stub_is_named_as_a_stub(self, db):
        db.execute(
            "INSERT INTO jobs (dedupe_hash, source, title, company, description, "
            "score) VALUES ('stub', 'test', 'Data Scientist', 'Achievers', "
            "'Data Scientist. Apply now.', 62)")
        db.commit()

        rows = audit(db, PROFILE, use_llm=False, everything=False)
        stub = next(r for r in rows if r["company"] == "Achievers")

        assert stub["described"] is False

    def test_a_real_posting_is_not(self, db):
        rows = audit(db, PROFILE, use_llm=False, everything=False)

        assert all(r["described"] for r in rows if r["company"] == "Shopify")
