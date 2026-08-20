"""Re-enriching Adzuna jobs already in the feed.

Enrichment runs during a fetch, so jobs saved before it existed still carry Adzuna's
truncated snippet. The maintenance endpoint fetches full descriptions for the short
Adzuna jobs whose link is fetchable, and re-scores them.
"""
from unittest.mock import patch


class TestEnrichExisting:
    def _add(self, conn, jid, source, url, desc, status="surfaced"):
        conn.execute(
            "INSERT INTO jobs (id, dedupe_hash, title, company, source, source_url, "
            "apply_url, description) OVERRIDING SYSTEM VALUE VALUES (?,?,?,?,?,?,?,?)",
            (jid, f"h{jid}", "Dev", "X", source, url, url, desc))
        conn.commit()

    def test_a_short_adzuna_job_gets_its_full_description(self, client, conn):
        self._add(conn, 1, "adzuna", "https://jobs.lever.co/acme/abc-123", "short")
        full = ("A full description that is well over the four hundred character floor "
                "the enricher wants to see before it believes a page is a real posting "
                "rather than a cookie wall. ") * 4

        with patch("src.enrich.full_description", return_value=full), \
             patch("src.routes.admin._get_setting", return_value="0"):   # scoring off
            body = client.post("/api/jobs/enrich-existing").json()

        assert body["enriched"] == 1
        desc = conn.execute("SELECT description FROM jobs WHERE id=1").fetchone()[0]
        assert len(desc) > 400

    def test_a_job_already_long_is_not_touched(self, client, conn):
        self._add(conn, 2, "adzuna", "https://jobs.lever.co/acme/abc-123", "x" * 500)
        with patch("src.routes.admin._get_setting", return_value="0"):
            body = client.post("/api/jobs/enrich-existing").json()
        assert body["checked"] == 0          # the query only selects short ones

    def test_a_non_adzuna_job_is_ignored(self, client, conn):
        self._add(conn, 3, "linkedin", "https://linkedin.com/x", "short")
        with patch("src.routes.admin._get_setting", return_value="0"):
            body = client.post("/api/jobs/enrich-existing").json()
        assert body["checked"] == 0

    def test_a_job_whose_link_is_not_fetchable_is_skipped(self, client, conn):
        self._add(conn, 4, "adzuna", "https://ca.indeed.com/viewjob?jk=1", "short")
        with patch("src.routes.admin._get_setting", return_value="0"):
            body = client.post("/api/jobs/enrich-existing").json()
        assert body["checked"] == 1          # it was a short adzuna job
        assert body["enriched"] == 0         # but indeed isn't fetchable


class TestEnrichDiagnosis:
    """The read-only tally of where short Adzuna links point."""

    def _add(self, conn, jid, url, desc="short"):
        conn.execute(
            "INSERT INTO jobs (id, dedupe_hash, title, company, source, source_url, "
            "apply_url, description) OVERRIDING SYSTEM VALUE "
            "VALUES (?,?,?,?,'adzuna',?,?,?)",
            (jid, f"h{jid}", "Dev", "X", url, url, desc))
        conn.commit()

    def test_it_counts_by_destination(self, client, conn):
        self._add(conn, 1, "https://jobs.lever.co/acme/abc-123")
        self._add(conn, 2, "https://boards.greenhouse.io/acme/jobs/456")
        self._add(conn, 3, "https://ca.indeed.com/viewjob?jk=1")
        self._add(conn, 4, "https://www.linkedin.com/jobs/view/2")

        d = client.get("/api/jobs/enrich-diagnosis").json()
        assert d["short_adzuna_jobs"] == 4
        assert d["fetchable"]["lever"] == 1
        assert d["fetchable"]["greenhouse"] == 1
        assert d["not_fetchable"] == 2
        hosts = {h["host"] for h in d["top_unfetchable_hosts"]}
        assert "indeed.com" in hosts or "ca.indeed.com" in hosts

    def test_a_long_complete_job_is_not_counted(self, client, conn):
        self._add(conn, 5, "https://jobs.lever.co/acme/x", desc="x" * 500)
        d = client.get("/api/jobs/enrich-diagnosis").json()
        assert d["short_adzuna_jobs"] == 0
