"""Normalising a raw job, and getting it into the database intact.

The regression this file exists for: `save_job` once listed only some of the
columns the schema has, so salary, job_type and deadline were computed by
`normalize` and then silently dropped on write. Nothing failed — the columns just
stayed NULL forever, which quietly disabled the `salary_floor` filter in
profile.yaml. A filter that never filters is worse than no filter, because you
believe it.

So the load-bearing test here is `test_save_job_writes_every_schema_column`: it
compares what the schema declares against what the INSERT names, and fails the
moment they drift apart again.
"""
import re
from pathlib import Path

import pytest

from src import store
from src.normalize import (
    normalize, parse_salary, dedupe_hash, is_valid, clean_html,
    MIN_PLAUSIBLE_SALARY,
)
from tests.conftest import make_job

ROOT = Path(__file__).resolve().parent.parent


class TestSalaryParsing:
    """Boards publish pay in whatever shape they like. None of them is a guess."""

    @pytest.mark.parametrize("raw,expected", [
        ({"min": 90000, "max": 120000},  (90000, 120000)),   # Ashby, structured
        ({"minValue": 80000, "maxValue": 95000}, (80000, 95000)),
        ("$90K - $120K",                 (90000, 120000)),   # Ashby summary
        ("$70,000 - $90,000",            (70000, 90000)),    # Remotive
        ("CA$100k–CA$140k",              (100000, 140000)),  # currency + en-dash
        ("120000",                       (120000, 120000)),  # a single figure
        (120000,                         (120000, 120000)),
    ])
    def test_reads_a_real_salary(self, raw, expected):
        assert parse_salary(raw) == expected

    @pytest.mark.parametrize("raw", [
        "$45 per hour",      # not comparable to an annual floor
        "$30/hr",
        "Competitive",       # nothing to parse
        "Posted 2026",       # a year is not a salary
        "Req #4821",         # nor is a reference number
        "",
        None,
    ])
    def test_refuses_to_guess(self, raw):
        """A wrong salary is worse than none: `salary_floor` filters on it."""
        assert parse_salary(raw) == (None, None)

    def test_implausible_figures_are_rejected(self):
        assert parse_salary(str(MIN_PLAUSIBLE_SALARY - 1)) == (None, None)
        lo, hi = parse_salary(str(MIN_PLAUSIBLE_SALARY))
        assert lo == MIN_PLAUSIBLE_SALARY


class TestNormalize:
    def test_carries_salary_through(self):
        job = normalize(make_job(salary="$80,000 - $100,000"))
        assert job["salary_min"] == 80000
        assert job["salary_max"] == 100000

    def test_explicit_salary_pair_wins_over_parsing(self):
        job = normalize(make_job(salary_min=75000, salary_max=95000,
                                 salary="ignore me"))
        assert (job["salary_min"], job["salary_max"]) == (75000, 95000)

    def test_remote_is_detected_from_location(self):
        assert normalize(make_job(location="Remote, Canada"))["remote"] == 1
        assert normalize(make_job(location="Toronto"))["remote"] == 0

    def test_job_type_defaults_rather_than_being_null(self):
        assert normalize(make_job(job_type=None))["job_type"] == "Unknown"

    def test_apply_url_falls_back_to_source_url(self):
        job = normalize({"title": "X", "company": "Y",
                         "source_url": "https://x/1", "apply_url": None})
        assert job["apply_url"] == "https://x/1"

    def test_description_html_is_cleaned_not_stripped(self):
        job = normalize(make_job(description="<script>evil()</script><p>Real text</p>"))
        assert "evil" not in job["description"]
        assert "Real text" in job["description"]


class TestDedupe:
    def test_same_role_at_same_company_is_one_job(self):
        """Boards title the same posting differently. The hash must not care."""
        a = dedupe_hash("Shopify Inc.", "Backend Developer (Remote)")
        b = dedupe_hash("Shopify", "Backend Developer")
        assert a == b

    def test_different_companies_are_different_jobs(self):
        assert dedupe_hash("Shopify", "Dev") != dedupe_hash("Lever", "Dev")


class TestValidity:
    def test_a_complete_job_is_valid(self):
        assert is_valid(normalize(make_job()))

    @pytest.mark.parametrize("missing", ["title", "company", "description",
                                         "location", "apply_url"])
    def test_a_job_missing_a_mandatory_field_is_not(self, missing):
        job = normalize(make_job())
        job[missing] = ""
        assert not is_valid(job)


class TestSaveJob:
    def test_round_trips_every_field(self, conn):
        job = normalize(make_job(salary="$80,000 - $100,000",
                                 deadline="2026-08-01"))
        job.update(score=85, skills_score=90, seniority_score=88,
                   domain_score=80, rationale="Good fit", flags=None)
        store.save_job(conn, job)
        conn.commit()

        row = conn.execute(
            "SELECT title, company, salary_min, salary_max, job_type, deadline, "
            "score FROM jobs WHERE dedupe_hash = ?", (job["dedupe_hash"],)
        ).fetchone()

        assert row == ("Junior Backend Developer", "Shopify", 80000, 100000,
                       "Full-time", "2026-08-01", 85)

    def test_save_job_writes_every_schema_column(self):
        """The regression guard.

        Every column the schema declares for `jobs` — except the ones the database
        fills itself — must appear in the INSERT. When someone adds a column to
        schema.sql and forgets `save_job`, this fails immediately instead of
        producing a silently NULL column six months later.
        """
        schema = (ROOT / "data" / "schema.sql").read_text(encoding="utf-8")
        block = re.search(r"CREATE TABLE IF NOT EXISTS\s+jobs\s*\((.*?)\n\);",
                          schema, re.S | re.I).group(1)

        declared = set()
        for line in block.splitlines():
            line = line.split("--")[0].strip().rstrip(",")
            if line and line.split()[0].isidentifier():
                declared.add(line.split()[0])

        # Columns the database owns, not the writer.
        db_owned = {"id", "fetched_at", "status", "applied_on", "notes",
                    "followed_up_on", "followup_snooze"}
        expected = declared - db_owned

        source = (ROOT / "src" / "store.py").read_text(encoding="utf-8")
        insert = re.search(r"INSERT OR IGNORE INTO jobs\s*\((.*?)\)\s*\n\s*VALUES",
                           source, re.S).group(1)
        named = set(re.findall(r"\w+", insert))

        missing = expected - named
        assert not missing, (
            f"save_job() does not write these columns: {sorted(missing)}. "
            f"They will stay NULL for every job. Add them to the INSERT."
        )


class TestCleanHtml:
    def test_strips_scripts_and_keeps_structure(self):
        out = clean_html("<div><script>x</script><ul><li>One</li></ul></div>")
        assert "script" not in out
        assert "<li>One</li>" in out

    def test_decodes_double_encoded_html(self):
        assert "<p>" in clean_html("&lt;p&gt;Hello&lt;/p&gt;")

    def test_empty_input_is_empty_output(self):
        assert clean_html(None) == ""
        assert clean_html("") == ""
