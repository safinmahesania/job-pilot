"""Documents, and the job they belong to.

The failure this guards against is the one that actually matters: a cover letter
addressed to Shopify arriving at League. Nothing in the app crashes when that
happens — an employer just reads it, and you never hear from them again.

The defence is structural rather than careful. A material is keyed by job_id and
`UNIQUE(job_id, kind)`, so a job has exactly one current resume and one current
cover letter — there is no stale pile to pick the wrong item from. The extension
requests files BY JOB ID, never by filename. And a page that doesn't confidently
match a job gets nothing attached at all: an empty upload slot you fill yourself
beats the wrong document every time.
"""
import pytest

from src import materials, store


def _job(conn, company, title="Backend Dev", url=None):
    conn.execute(
        "INSERT INTO jobs (dedupe_hash, source, title, company, apply_url, status) "
        "VALUES (?,?,?,?,?,'saved')",
        (company + title, "test", title, company,
         url or f"https://boards.greenhouse.io/{company.lower()}/jobs/1"),
    )
    conn.commit()
    return conn.execute("SELECT id FROM jobs WHERE company=?", (company,)).fetchone()[0]


class TestBinding:
    def test_a_document_belongs_to_exactly_one_job(self, conn):
        shopify = _job(conn, "Shopify")
        league = _job(conn, "League")

        materials.save(shopify, "cover", "Dear Shopify...", "gemini")

        assert "Shopify" in materials.get(shopify, "cover")["content"]
        assert materials.get(league, "cover") is None, (
            "League has no cover letter — nothing may be served for it"
        )

    def test_regenerating_replaces_rather_than_accumulates(self, conn):
        """One current document per job. No pile, no wrong pick."""
        job = _job(conn, "Shopify")

        materials.save(job, "cover", "First draft", "ollama")
        materials.save(job, "cover", "Better draft", "gemini")

        assert materials.get(job, "cover")["content"] == "Better draft"
        assert materials.get(job, "cover")["provider"] == "gemini"
        assert len(materials.list_for(job)) == 1

    def test_resume_and_cover_coexist(self, conn):
        job = _job(conn, "Shopify")
        materials.save(job, "resume", "# Safin", "gemini")
        materials.save(job, "cover", "Dear...", "gemini")

        assert {m["kind"] for m in materials.list_for(job)} == {"resume", "cover"}

    def test_an_unknown_kind_is_refused(self, conn):
        job = _job(conn, "Shopify")
        with pytest.raises(ValueError):
            materials.save(job, "portfolio", "...", "gemini")

    def test_deleting_a_job_takes_its_documents_with_it(self, conn):
        job = _job(conn, "Shopify")
        materials.save(job, "cover", "Dear Shopify", "gemini")

        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM jobs WHERE id = ?", (job,))
        conn.commit()

        assert materials.get(job, "cover") is None


class TestFilenames:
    def test_it_names_the_company_it_is_for(self, conn, monkeypatch, profile):
        from src import config
        monkeypatch.setattr(config, "load_profile", lambda: profile)

        name = materials.filename({"company": "Shopify Inc."}, "resume", "pdf")

        assert "Shopify" in name
        assert name.endswith(".pdf")

    def test_it_survives_a_company_name_full_of_punctuation(self, conn,
                                                            monkeypatch, profile):
        from src import config
        monkeypatch.setattr(config, "load_profile", lambda: profile)

        name = materials.filename({"company": "Ben & Jerry's (Canada), Inc."},
                                  "cover", "pdf")

        assert "/" not in name and "&" not in name and "'" not in name
        assert name.endswith(".pdf")


class TestPdf:
    def test_it_produces_a_real_pdf(self):
        pdf = materials.to_pdf("# Safin\n\n## Skills\n\n- Python\n", "resume")
        assert pdf[:4] == b"%PDF"
        assert len(pdf) > 500

    def test_template_instructions_never_reach_the_page(self):
        """The resume template carries an HTML comment explaining itself. A stray
        line of it on a real resume would be mortifying."""
        markdown = (
            "# Safin\n\n## Skills\n\n- Python\n\n"
            "<!--\nHOW THIS TEMPLATE WORKS\nReplace the placeholders.\n-->\n"
        )
        pdf = materials.to_pdf(markdown, "resume")

        assert b"HOW THIS TEMPLATE WORKS" not in pdf
        assert b"Replace the placeholders" not in pdf

    def test_smart_punctuation_does_not_break_the_encoder(self):
        """Models love em-dashes and curly quotes; core PDF fonts are latin-1."""
        text = "I\u2019m keen \u2014 \u201cgenuinely\u201d \u2026 and available."
        pdf = materials.to_pdf(text, "cover")
        assert pdf[:4] == b"%PDF"
