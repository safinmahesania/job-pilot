"""What you sent for a job, kept and readable.

A resume and cover letter were already stored. The screening answers were not — and
those are the part you have no other copy of, and the part an interviewer is most
likely to ask you to expand on. This covers both halves: the answers being kept when
autofill resolves them, and the one endpoint that hands back the whole application.
"""
from unittest.mock import patch


class TestAnswersAreKept:
    def _job(self, conn):
        conn.execute(
            "INSERT INTO jobs (dedupe_hash, title, company, status) "
            "VALUES ('a1', 'Junior Developer', 'Shopify', 'applied')")
        conn.commit()
        return conn.execute("SELECT id FROM jobs WHERE dedupe_hash='a1'").fetchone()[0]

    def _resolve(self, client, job_id, fields, answers):
        with patch("src.autofill.resolve", return_value=answers):
            return client.post("/api/autofill/resolve",
                               json={"fields": fields, "job_id": job_id})

    def test_resolved_answers_are_stored_against_the_job(self, client, conn):
        jid = self._job(conn)
        r = self._resolve(
            client, jid,
            [{"id": "f0", "label": "Why do you want to work here?", "type": "textarea"}],
            {"f0": "Because I have built things with your stack."})
        assert r.status_code == 200

        rows = conn.execute(
            "SELECT question, answer FROM application_answers WHERE job_id=?", (jid,)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "Why do you want to work here?"
        assert "built things" in rows[0][1]

    def test_blank_answers_are_not_stored(self, conn, client):
        jid = self._job(conn)
        self._resolve(client, jid,
                      [{"id": "f0", "label": "Salary expectation", "type": "text"}],
                      {"f0": ""})
        n = conn.execute(
            "SELECT COUNT(*) FROM application_answers WHERE job_id=?", (jid,)).fetchone()[0]
        assert n == 0        # a field the model declined to invent is not an answer

    def test_filling_the_same_form_again_updates_rather_than_duplicates(self, conn, client):
        jid = self._job(conn)
        q = [{"id": "f0", "label": "Why here?", "type": "textarea"}]
        self._resolve(client, jid, q, {"f0": "First answer."})
        self._resolve(client, jid, q, {"f0": "Second, better answer."})

        rows = conn.execute(
            "SELECT answer FROM application_answers WHERE job_id=?", (jid,)).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "Second, better answer."

    def test_answers_without_a_job_are_not_stored_anywhere(self, conn, client):
        """The popup's ask-a-question box can run with no job bound. Nothing to file it
        under, so nothing is filed — rather than a row pointing at nothing."""
        with patch("src.autofill.resolve", return_value={"f0": "An answer."}):
            r = client.post("/api/autofill/resolve", json={
                "fields": [{"id": "f0", "label": "Anything?", "type": "text"}]})
        assert r.status_code == 200
        assert conn.execute("SELECT COUNT(*) FROM application_answers").fetchone()[0] == 0


class TestTheApplicationRecord:
    def test_it_returns_documents_and_answers_together(self, client, conn):
        conn.execute(
            "INSERT INTO jobs (dedupe_hash, title, company, status) "
            "VALUES ('a2', 'Dev', 'Acme Co', 'applied')")
        conn.commit()
        jid = conn.execute("SELECT id FROM jobs WHERE dedupe_hash='a2'").fetchone()[0]

        conn.execute("INSERT INTO materials (job_id, kind, content, provider) "
                     "VALUES (?, 'cover', 'Dear hiring manager...', 'gemini')", (jid,))
        conn.execute("INSERT INTO application_answers (job_id, question, answer) "
                     "VALUES (?, 'Why us?', 'Because of the product.')", (jid,))
        conn.commit()

        body = client.get(f"/api/jobs/{jid}/application").json()
        assert body["job"]["company"] == "Acme Co"
        assert "Dear hiring manager" in body["materials"]["cover"]["content"]
        assert body["answers"][0]["question"] == "Why us?"

    def test_an_unknown_job_is_a_404(self, client):
        assert client.get("/api/jobs/999999/application").status_code == 404

    def test_a_job_with_nothing_sent_yet_is_empty_not_an_error(self, client, conn):
        conn.execute("INSERT INTO jobs (dedupe_hash, title, company) "
                     "VALUES ('a3', 'Dev', 'X')")
        conn.commit()
        jid = conn.execute("SELECT id FROM jobs WHERE dedupe_hash='a3'").fetchone()[0]
        body = client.get(f"/api/jobs/{jid}/application").json()
        assert body["materials"] == {}
        assert body["answers"] == []
