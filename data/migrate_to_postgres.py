"""One-time migration: the old single-user SQLite database → multi-user Postgres.

The old app kept one person's world in `data/jobpilot.db`: jobs carried both the
posting AND that person's judgement (score, status, notes, follow-ups), and
materials / answers / notifications had no owner because there was only one.

The new model splits that in two. `jobs` is a SHARED pool of postings; each
person's judgement lives in `user_jobs`, keyed by their Supabase user id. So this
script needs a user to attribute the old data to — the admin who owned the SQLite
file. That user must already exist in Supabase (sign up first); pass their email
or uuid.

What it does, in one transaction:

  * jobs   → the shared pool (posting + extracted fields only), keeping the old ids
             so materials / answers / user_jobs line up without a remap.
  * the judgement columns (score/status/notes/applied_on/…) → user_jobs for the
             admin, one row per old job.
  * materials, application_answers, notifications → the same rows, now owned by
             the admin.
  * seen, source_health, runs, errors, llm_usage → copied as-is (still global).
  * settings → app_settings (global), except score_threshold which is now
             per-user and lands in user_settings for the admin.
  * config/profile.yaml → user_profiles for the admin (the whole file as JSON).

Re-runnable: every insert is ON CONFLICT DO NOTHING / upsert, so a second run on
a partially-migrated database fills gaps rather than erroring or duplicating.

Usage:
    export DATABASE_URL="postgresql://...supabase..."
    python data/migrate_to_postgres.py --sqlite data/jobpilot.db --email you@example.com
    # or: --user-id 11111111-1111-1111-1111-111111111111
"""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

import psycopg


# ── column lists (target order) ─────────────────────────────────────────────

# Pool: posting + extraction only. quality_flags comes from the old `flags`.
JOB_COLS = [
    "id", "dedupe_hash", "source", "source_url", "apply_url", "title", "company",
    "location", "remote", "salary_min", "salary_max", "description", "posted_date",
    "fetched_at", "deadline", "job_type", "language", "quality_flags",
    "work_mode", "seniority_level", "location_detail", "salary_text", "benefits",
    "responsibilities", "requirements", "nice_to_have", "tech_stack",
    "about_company", "instructions", "extracted_at",
]
# Which target columns need an explicit cast from the text SQLite carries.
JOB_TS = {"fetched_at", "extracted_at"}
JOB_JSONB = {"quality_flags"}          # old `flags` JSON string
JOB_BOOL = {"remote"}                  # old 0/1

# The judgement, per user.
UJ_COLS = [
    "score", "skills_score", "seniority_score", "domain_score", "rationale",
    "status", "applied_on", "notes", "last_viewed_at", "followed_up_on",
    "followup_snooze",
]
UJ_TS = {"applied_on", "last_viewed_at", "followed_up_on", "followup_snooze"}


def _bool(v):
    return None if v is None else bool(v)


def _placeholder(col, ts: set, jsonb: set) -> str:
    """A VALUES placeholder, wrapped in the cast the target column needs.

    Empty strings become NULL (NULLIF) so a blank SQLite cell doesn't fail a
    timestamptz/jsonb cast.
    """
    if col in ts:
        return "NULLIF(%s,'')::timestamptz"
    if col in jsonb:
        return "NULLIF(%s,'')::jsonb"
    return "%s"


def _resolve_admin(pg, email: str | None, user_id: str | None) -> str:
    if user_id:
        row = pg.execute("SELECT id FROM public.users WHERE id=%s", (user_id,)).fetchone()
        if not row:
            sys.exit(f"No public.users row for id {user_id}. Sign up in Supabase first.")
        return str(row[0])
    row = pg.execute("SELECT id FROM public.users WHERE email=%s", (email,)).fetchone()
    if not row:
        sys.exit(f"No public.users row for {email}. Sign up in Supabase first, "
                 "then re-run.")
    return str(row[0])


def migrate(sqlite_path: str, dsn: str, email: str | None, user_id: str | None,
            profile_path: str | None) -> dict:
    if not Path(sqlite_path).exists():
        sys.exit(f"SQLite database not found: {sqlite_path}")

    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row

    counts: dict[str, int] = {}
    pg = psycopg.connect(dsn)
    try:
        with pg.transaction():
            admin = _resolve_admin(pg, email, user_id)

            # ── jobs → pool (ids preserved) ──
            job_ph = ", ".join(_placeholder(c, JOB_TS, JOB_JSONB) for c in JOB_COLS)
            job_sql = (
                f"INSERT INTO public.jobs ({', '.join(JOB_COLS)}) "
                f"OVERRIDING SYSTEM VALUE VALUES ({job_ph}) "
                "ON CONFLICT (id) DO NOTHING"
            )
            n = 0
            for r in src.execute("SELECT * FROM jobs"):
                vals = []
                for c in JOB_COLS:
                    src_col = "flags" if c == "quality_flags" else c
                    v = r[src_col] if src_col in r.keys() else None
                    if c in JOB_BOOL:
                        v = _bool(v)
                    vals.append(v)
                pg.execute(job_sql, vals)
                n += 1
            counts["jobs"] = n

            # Keep the identity sequence ahead of the ids we forced in.
            pg.execute(
                "SELECT setval(pg_get_serial_sequence('public.jobs','id'), "
                "GREATEST((SELECT COALESCE(MAX(id),1) FROM public.jobs), 1))"
            )

            # The set of job ids that actually made it into the pool. The old SQLite
            # file can carry orphaned materials/answers (SQLite doesn't enforce FKs by
            # default), so anything pointing at a job that isn't here must be skipped —
            # Postgres will reject the FK otherwise.
            pool_ids = {r[0] for r in pg.execute("SELECT id FROM public.jobs").fetchall()}

            # ── judgement → user_jobs (admin) ──
            uj_targets = ["user_id", "job_id", "served_at", *UJ_COLS]
            uj_ph = ["%s", "%s", "NULLIF(%s,'')::timestamptz"]
            uj_ph += [_placeholder(c, UJ_TS, set()) for c in UJ_COLS]
            uj_sql = (
                f"INSERT INTO public.user_jobs ({', '.join(uj_targets)}) "
                f"VALUES ({', '.join(uj_ph)}) "
                "ON CONFLICT (user_id, job_id) DO NOTHING"
            )
            n = 0
            for r in src.execute("SELECT * FROM jobs"):
                served_at = r["fetched_at"] if "fetched_at" in r.keys() else None
                vals = [admin, r["id"], served_at]
                for c in UJ_COLS:
                    v = r[c] if c in r.keys() else None
                    if c == "status" and not v:
                        v = "surfaced"
                    vals.append(v)
                pg.execute(uj_sql, vals)
                n += 1
            counts["user_jobs"] = n

            # ── materials (admin) ──
            counts["materials"] = 0
            if _table_exists(src, "materials"):
                for r in src.execute("SELECT * FROM materials"):
                    if r["job_id"] not in pool_ids:
                        continue
                    pg.execute(
                        "INSERT INTO public.materials "
                        "(user_id, job_id, kind, content, provider, created_at) "
                        "VALUES (%s,%s,%s,%s,%s, NULLIF(%s,'')::timestamptz) "
                        "ON CONFLICT (user_id, job_id, kind) DO NOTHING",
                        (admin, r["job_id"], r["kind"], r["content"],
                         r["provider"], r["created_at"]),
                    )
                    counts["materials"] += 1

            # ── application_answers (admin) ──
            counts["application_answers"] = 0
            for r in src.execute("SELECT * FROM application_answers"):
                if r["job_id"] not in pool_ids:
                    continue
                pg.execute(
                    "INSERT INTO public.application_answers "
                    "(user_id, job_id, question, answer, created_at) "
                    "VALUES (%s,%s,%s,%s, NULLIF(%s,'')::timestamptz) "
                    "ON CONFLICT (user_id, job_id, question) DO NOTHING",
                    (admin, r["job_id"], r["question"], r["answer"], r["created_at"]),
                )
                counts["application_answers"] += 1

            # ── notifications (admin) ── append-only log: only on a clean target
            counts["notifications"] = 0
            if _pg_empty(pg, "public.notifications"):
              for r in src.execute("SELECT * FROM notifications"):
                pg.execute(
                    "INSERT INTO public.notifications (user_id, text, created_at, seen) "
                    "VALUES (%s,%s, NULLIF(%s,'')::timestamptz, %s)",
                    (admin, r["text"], r["created_at"], _bool(r["seen"])),
                )
                counts["notifications"] += 1

            # ── seen (global) ──
            counts["seen"] = 0
            for r in src.execute("SELECT * FROM seen"):
                pg.execute(
                    "INSERT INTO public.seen (dedupe_hash, decision, score, first_seen) "
                    "VALUES (%s,%s,%s, NULLIF(%s,'')::timestamptz) "
                    "ON CONFLICT (dedupe_hash) DO NOTHING",
                    (r["dedupe_hash"], r["decision"], r["score"], r["first_seen"]),
                )
                counts["seen"] += 1

            # ── source_health (global) ──
            counts["source_health"] = 0
            for r in src.execute("SELECT * FROM source_health"):
                pg.execute(
                    "INSERT INTO public.source_health "
                    "(name, ats, fetched, kept, status, error, last_run, "
                    " zero_streak, error_streak, last_ok, alerted) "
                    "VALUES (%s,%s,%s,%s,%s,%s, NULLIF(%s,'')::timestamptz, "
                    " %s,%s, NULLIF(%s,'')::timestamptz, %s) "
                    "ON CONFLICT (name) DO NOTHING",
                    (r["name"], r["ats"], r["fetched"], r["kept"], r["status"],
                     r["error"], r["last_run"], r["zero_streak"], r["error_streak"],
                     r["last_ok"], _bool(r["alerted"])),
                )
                counts["source_health"] += 1

            # ── runs (global) ── append-only log: only on a clean target
            counts["runs"] = 0
            if _pg_empty(pg, "public.runs"):
              for r in src.execute("SELECT * FROM runs"):
                pg.execute(
                    "INSERT INTO public.runs "
                    "(started_at, kind, fetched, seen, dropped, trashed, kept, errors) "
                    "VALUES (NULLIF(%s,'')::timestamptz,%s,%s,%s,%s,%s,%s,%s)",
                    (r["started_at"], r["kind"], r["fetched"], r["seen"],
                     r["dropped"], r["trashed"], r["kept"], r["errors"]),
                )
                counts["runs"] += 1

            # ── errors (global) ──
            counts["errors"] = 0
            if _table_exists(src, "errors") and _pg_empty(pg, "public.errors"):
                for r in src.execute("SELECT * FROM errors"):
                    pg.execute(
                        "INSERT INTO public.errors "
                        "(at, where_, kind, message, traceback, notified) "
                        "VALUES (NULLIF(%s,'')::timestamptz,%s,%s,%s,%s,%s)",
                        (r["at"], r["where_"], r["kind"], r["message"],
                         r["traceback"], _bool(r["notified"])),
                    )
                    counts["errors"] += 1

            # ── llm_usage (global) ──
            counts["llm_usage"] = 0
            if _table_exists(src, "llm_usage"):
                for r in src.execute("SELECT * FROM llm_usage"):
                    pg.execute(
                        "INSERT INTO public.llm_usage (day, provider, tokens, requests) "
                        "VALUES (%s,%s,%s,%s) ON CONFLICT (day, provider) DO NOTHING",
                        (r["day"], r["provider"], r["tokens"], r["requests"]),
                    )
                    counts["llm_usage"] += 1

            # ── settings → app_settings (+ score_threshold to user_settings) ──
            counts["app_settings"] = 0
            for r in src.execute("SELECT key, value FROM settings"):
                if r["key"] == "score_threshold":
                    pg.execute(
                        "INSERT INTO public.user_settings (user_id, key, value) "
                        "VALUES (%s,'score_threshold',%s) "
                        "ON CONFLICT (user_id, key) DO UPDATE SET value=excluded.value",
                        (admin, r["value"]),
                    )
                    continue
                pg.execute(
                    "INSERT INTO public.app_settings (key, value) VALUES (%s,%s) "
                    "ON CONFLICT (key) DO UPDATE SET value=excluded.value",
                    (r["key"], r["value"]),
                )
                counts["app_settings"] += 1

            # ── profile.yaml → user_profiles (admin) ──
            counts["user_profiles"] = 0
            if profile_path and Path(profile_path).exists():
                import yaml
                data = yaml.safe_load(Path(profile_path).read_text()) or {}
                pg.execute(
                    "INSERT INTO public.user_profiles (user_id, profile, updated_at) "
                    "VALUES (%s, %s::jsonb, now()) "
                    "ON CONFLICT (user_id) DO UPDATE SET "
                    "  profile=excluded.profile, updated_at=now()",
                    (admin, json.dumps(data)),
                )
                counts["user_profiles"] = 1

        counts["_admin"] = admin
        return counts
    finally:
        pg.close()
        src.close()


def _pg_empty(pg, table: str) -> bool:
    return pg.execute(f"SELECT NOT EXISTS (SELECT 1 FROM {table})").fetchone()[0]


def _table_exists(src, name: str) -> bool:
    return src.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def main():
    ap = argparse.ArgumentParser(description="Migrate SQLite JobPilot to Postgres.")
    ap.add_argument("--sqlite", default="data/jobpilot.db")
    ap.add_argument("--email", help="admin's Supabase account email")
    ap.add_argument("--user-id", help="admin's Supabase user uuid (instead of email)")
    ap.add_argument("--profile", default="config/profile.yaml")
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = ap.parse_args()

    if not args.database_url:
        sys.exit("Set DATABASE_URL (or pass --database-url).")
    if not args.email and not args.user_id:
        sys.exit("Pass --email or --user-id (the admin to attribute old data to).")

    counts = migrate(args.sqlite, args.database_url, args.email, args.user_id,
                     args.profile)

    admin = counts.pop("_admin")
    print(f"\nMigrated into Postgres, attributed to user {admin}:")
    for k, v in counts.items():
        print(f"  {k:22} {v}")
    print("\nDone. Remember to mark the admin: "
          "update public.users set is_admin=true where id='" + admin + "';")


if __name__ == "__main__":
    main()
