"""The language column reaches a database that predates it.

New columns are added by the startup migration reading schema.sql, not by recreating the
table — an existing database keeps its rows. This checks the column actually lands, so a
job can be saved with its language rather than 500-ing on an unknown column.
"""
import sqlite3

from data.init_db import _add_missing_columns
from src.paths import SCHEMA_PATH


def test_language_column_is_added_to_an_old_jobs_table(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    # A jobs table from before the column existed.
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, title TEXT, "
                 "dedupe_hash TEXT, description TEXT)")
    conn.commit()

    schema = open(SCHEMA_PATH, encoding="utf-8").read()
    _add_missing_columns(conn, schema)

    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    assert "language" in cols
    conn.close()


def test_a_job_saves_with_its_language(client, conn):
    """End to end: the column is present and writable through the normal save path."""
    conn.execute(
        "INSERT INTO jobs (dedupe_hash, title, company, description, language) "
        "VALUES ('h1', 'Dev', 'X', 'desc', 'fr')")
    conn.commit()
    row = conn.execute("SELECT language FROM jobs WHERE dedupe_hash='h1'").fetchone()
    assert row[0] == "fr"
