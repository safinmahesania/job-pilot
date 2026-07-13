"""Getting jobs in from outside the fetch pipeline.

The invariant that matters here is honesty about what wasn't imported. LinkedIn
and Indeed put their job pages behind a login wall, so an alert email gives you a
title, a company and a tracking link — and no description. A scoring model handed
a title and nothing else will still return a confident 78, and that number is
worthless.

So: no description, no score. The job is stored unscored and shown in its own tab
for you to judge. Every test below that mentions "unscored" is defending that.
"""
import io

import pytest

from src import importers


class TestCsv:
    def test_it_recognises_the_columns_people_actually_use(self):
        """Nobody's spreadsheet has a column called `apply_url`."""
        csv = (b"Job Title,Employer,City,Link,Job Description\n"
               b"Backend Developer,Shopify,Toronto,https://x/1,Python and FastAPI\n")

        rows = importers.parse_tabular(csv, "jobs.csv")

        assert len(rows) == 1
        assert rows[0]["title"] == "Backend Developer"
        assert rows[0]["company"] == "Shopify"
        assert rows[0]["location"] == "Toronto"
        assert rows[0]["apply_url"] == "https://x/1"

    @pytest.mark.parametrize("header", [
        b"title,company\n", b"position,employer\n", b"role,organisation\n",
        b"Job Title,Company Name\n",
    ])
    def test_title_and_company_are_found_however_they_are_spelled(self, header):
        rows = importers.parse_tabular(header + b"Dev,Shopify\n", "jobs.csv")
        assert rows[0]["title"] == "Dev"
        assert rows[0]["company"] == "Shopify"

    def test_a_row_without_a_title_is_dropped_not_guessed(self):
        csv = b"title,company\n,Shopify\nDev,Lever\n"
        rows = importers.parse_tabular(csv, "jobs.csv")
        assert len(rows) == 1
        assert rows[0]["company"] == "Lever"

    def test_an_empty_file_yields_no_rows(self):
        """The parser reports nothing found; the API turns that into a 400 (see
        test_api). Keeping the parser quiet keeps the layers honest."""
        assert importers.parse_tabular(b"title,company\n", "jobs.csv") == []

    def test_a_file_with_no_recognisable_columns_yields_no_rows(self):
        assert importers.parse_tabular(b"foo,bar\n1,2\n", "jobs.csv") == []

    def test_binary_junk_yields_no_rows_rather_than_crashing(self):
        assert importers.parse_tabular(b"\x00\x01\x02not a csv", "jobs.csv") == []


class TestPastedText:
    def test_it_extracts_what_the_model_finds(self, conn, monkeypatch,
                                              capture_llm):
        capture_llm.reply = lambda s, u: (
            '{"title":"Backend Developer","company":"Shopify",'
            '"location":"Toronto","apply_url":"https://x/1",'
            '"description":"Python and FastAPI"}', "gemini")

        job = importers.parse_text("Backend Developer at Shopify. " * 10)

        assert job["title"] == "Backend Developer"
        assert job["company"] == "Shopify"

    def test_it_refuses_a_paste_too_short_to_be_a_posting(self):
        with pytest.raises(ValueError):
            importers.parse_text("hi")

    def test_a_field_the_model_could_not_find_is_never_invented(self, conn,
                                                                monkeypatch,
                                                                capture_llm):
        """An explicit "not specified" is a fact. A plausible city is a lie you
        would then act on."""
        capture_llm.reply = lambda s, u: (
            '{"title":"Backend Developer","company":"Shopify",'
            '"location":"","apply_url":"","description":""}', "gemini")

        job = importers.parse_text("Backend Developer at Shopify. " * 10)

        assert job["location"] == "Not specified"
        assert job["apply_url"] == ""
        assert job["description"] == ""


class TestScoringOnImport:
    def _import(self, conn, monkeypatch, rows, description):
        monkeypatch.setattr(importers, "recover_description",
                            lambda url: description)
        return rows

    def test_a_job_with_a_description_gets_scored(self, conn, monkeypatch,
                                                  profile, capture_llm):
        from src.scoring import rerank
        monkeypatch.setattr(importers, "load_profile", lambda: profile)
        monkeypatch.setattr(rerank, "scoring_via_chain", lambda: True)
        capture_llm.reply = lambda s, u: (
            '{"skills_score":90,"seniority_score":85,"domain_score":80,'
            '"overall":70,"rationale":"fits"}', "cerebras")

        result = importers.import_jobs([{
            "title": "Backend Developer", "company": "Shopify",
            "location": "Toronto", "apply_url": "https://boards.greenhouse.io/x/1",
            "description": "Python, FastAPI, PostgreSQL. New grads welcome.",
        }])

        assert result["scored"] == 1
        assert result["unscored"] == 0

    def test_a_job_with_no_description_is_stored_unscored(self, conn, monkeypatch,
                                                          profile, capture_llm):
        """The whole point. A title alone cannot be scored honestly."""
        monkeypatch.setattr(importers, "load_profile", lambda: profile)
        monkeypatch.setattr(importers, "recover_description", lambda url: "")

        result = importers.import_jobs([{
            "title": "Backend Developer", "company": "Shopify",
            "location": "Toronto",
            "apply_url": "https://linkedin.com/comm/jobs/view/1",
            "description": "",
        }])

        assert result["unscored"] == 1
        assert result["scored"] == 0

        row = conn.execute(
            "SELECT score, status FROM jobs WHERE company='Shopify'").fetchone()
        assert row[0] is None, "an unscoreable job was given a score anyway"

    def test_importing_the_same_job_twice_is_a_duplicate_not_a_second_row(
            self, conn, monkeypatch, profile):
        monkeypatch.setattr(importers, "load_profile", lambda: profile)
        monkeypatch.setattr(importers, "recover_description", lambda url: "")

        rows = [{"title": "Backend Developer", "company": "Shopify",
                 "location": "Toronto", "apply_url": "https://x/1",
                 "description": ""}]

        importers.import_jobs(rows)
        second = importers.import_jobs(rows)

        assert second["duplicates"] == 1
        assert second["imported"] == 0
        count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert count == 1
