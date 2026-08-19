"""Postgres connection layer (psycopg3) with a SQLite-compatible surface.

The whole codebase was written against sqlite3: it calls ``conn.execute("… ? …",
params)``, reads a row as both ``row[0]`` and ``row["col"]``, commits explicitly,
and groups writes with ``with conn:``. Rather than rewrite all 117 call sites at
once, this module lets that surface keep working against Supabase Postgres by:

  * translating ``?`` placeholders to psycopg's ``%s`` (and literal ``%`` → ``%%``
    when params are present, which psycopg requires — verified against PG16),
  * translating SQLite's ``datetime('now')`` to Postgres ``now()``,
  * returning rows that support integer AND string indexing, like ``sqlite3.Row``,
  * keeping explicit-commit / ``with conn:`` semantics (autocommit stays OFF, so
    the existing ``conn.commit()`` calls and ``with conn:`` blocks behave as before).

Structural SQLite-isms that can't be translated blindly — ``INSERT OR IGNORE``,
``.lastrowid``, ``strftime``/``julianday`` — are fixed per query as each module is
ported; they are NOT handled here.

Connection string: read from the DATABASE_URL env var (a Supabase *direct*
connection string, e.g. postgresql://postgres:…@db.<ref>.supabase.co:5432/postgres).
"""
from __future__ import annotations

import os
import re

from psycopg import Connection

# SQLite named params (:name) → psycopg (%(name)s). Restricted to identifiers that
# start with a letter/underscore so it never touches Python-ish ':500' slices,
# '::' casts, or '12:30' time literals. The negative lookbehind skips '::' and
# mid-word colons (e.g. the ':' in 'http:').
_NAMED_PARAM = re.compile(r"(?<![:\w]):([a-zA-Z_]\w*)")


class HybridRow:
    """A result row that indexes by position OR column name, like sqlite3.Row.

    ``row[0]`` and ``row["title"]`` both work; iterating yields values (so tuple
    unpacking still works), and ``dict(row)`` / ``.keys()`` / ``.get()`` are there
    for the code paths that expect a mapping.
    """

    __slots__ = ("_cols", "_vals", "_map")

    def __init__(self, cols: list[str], vals: tuple):
        self._cols = cols
        self._vals = vals
        self._map: dict | None = None

    def _mapping(self) -> dict:
        if self._map is None:
            self._map = dict(zip(self._cols, self._vals))
        return self._map

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._vals[key]
        return self._mapping()[key]

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def keys(self):
        return list(self._cols)

    def get(self, key, default=None):
        return self._mapping().get(key, default)

    def __contains__(self, key):
        return key in self._mapping()

    def __repr__(self):
        return f"HybridRow({self._mapping()!r})"


def _hybrid_row_factory(cursor):
    """psycopg row_factory: build a HybridRow per result row."""
    desc = cursor.description
    cols = [c.name for c in desc] if desc else []

    def make(values):
        return HybridRow(cols, values)

    return make


def _has_params(params) -> bool:
    if params is None:
        return False
    try:
        return len(params) > 0
    except TypeError:
        return True


def _translate(sql: str, params=None) -> str:
    """Rewrite a SQLite-style query into psycopg's paramstyle.

    ``datetime('now')`` → ``now()`` always. When params are present, literal ``%``
    must be doubled (psycopg requirement) and ``?`` becomes ``%s``. With no params,
    ``%`` is left alone (psycopg treats it literally) — this keeps ``LIKE '%…'``
    working in the handful of no-param queries that use it. ``:name`` named params
    become ``%(name)s`` either way.
    """
    sql = sql.replace("datetime('now')", "now()")
    if _has_params(params):
        sql = sql.replace("%", "%%").replace("?", "%s")
    else:
        sql = sql.replace("?", "%s")
    sql = _NAMED_PARAM.sub(r"%(\1)s", sql)
    return sql


class CompatConnection(Connection):
    """A psycopg Connection that accepts the SQLite call surface the code uses."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # sqlite3's ``with conn:`` commits (or rolls back) but LEAVES THE CONNECTION
        # OPEN. psycopg's default __exit__ also closes it, which would break the 14
        # ``with conn:`` blocks that keep using the connection afterwards. Match
        # sqlite3: commit/rollback, don't close.
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False

    def execute(self, query, params=None, **kwargs):
        return super().execute(_translate(query, params), params, **kwargs)

    def executemany(self, query, params_seq, **kwargs):
        # sqlite3 exposes executemany on the connection; psycopg puts it on the
        # cursor. Translate once (the first row tells us params are present) and
        # run it through a cursor.
        cur = self.cursor()
        cur.executemany(_translate(query, params_seq[0] if params_seq else None),
                        params_seq, **kwargs)
        return cur

    def executescript(self, script: str):
        # sqlite3's multi-statement helper. psycopg runs a semicolon-separated
        # batch in a single execute when there are no parameters.
        return super().execute(_translate(script, None))

    # deps.py sets ``conn.row_factory = sqlite3.Row``; we always use HybridRow, so
    # accept the assignment and ignore it rather than letting sqlite3.Row through.
    @property
    def row_factory(self):
        return self._row_factory

    @row_factory.setter
    def row_factory(self, value):
        # keep HybridRow; swallow attempts to set sqlite3.Row
        if callable(value) and value is not None and value.__class__.__name__ != "type":
            self._row_factory = value


def connect(dsn: str | None = None) -> CompatConnection:
    """Open a Postgres connection with the SQLite-compatible surface.

    autocommit stays False so the code's explicit ``conn.commit()`` calls and
    ``with conn:`` blocks keep their meaning. HybridRow is the default row type.
    """
    dsn = dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL is not set — point it at your Supabase direct "
            "connection string (postgresql://postgres:…@db.<ref>.supabase.co:5432/postgres)."
        )
    return CompatConnection.connect(
        dsn,
        autocommit=False,
        row_factory=_hybrid_row_factory,
    )
