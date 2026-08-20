"""JobPilot post-migration smoke test.

Runs a broad, mostly read-only health check against your REAL Supabase Postgres and
the current code, then prints a report you can paste back for analysis. It does not
write to your data (the one optional write — a get-new dry run — is off by default).

    python smoke_test.py                 # full read-only check
    python smoke_test.py --token "<jwt>" # also verify a real Supabase token
        (get the token from the browser console: await window.jobpilotAuth.token())

Nothing here deletes or rescores anything. If a check can't run, it's reported and
the rest continue.
"""
import argparse
import json
import os
import sys
import traceback
import urllib.request

# ── report helpers ───────────────────────────────────────────────────────────

RESULTS = []  # (level, name, detail)


def ok(name, detail=""):
    RESULTS.append(("PASS", name, detail))
    print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))


def warn(name, detail=""):
    RESULTS.append(("WARN", name, detail))
    print(f"  [WARN] {name}" + (f" — {detail}" if detail else ""))


def fail(name, detail=""):
    RESULTS.append(("FAIL", name, detail))
    print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def section(t):
    print(f"\n=== {t} ===")


def run(name, fn, level_on_error="FAIL"):
    """Run a check function; PASS on truthy/None return, report exceptions."""
    try:
        detail = fn()
        ok(name, detail if isinstance(detail, str) else "")
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        (fail if level_on_error == "FAIL" else warn)(name, msg)


# ── the checks ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", help="a real Supabase access token to verify")
    ap.add_argument("--get-new-dry", action="store_true",
                    help="also count get-new candidates (still read-only)")
    args = ap.parse_args()

    print("JobPilot smoke test")
    print("Python:", sys.version.split()[0], "| cwd:", os.getcwd())

    # 1) ENV -------------------------------------------------------------------
    section("1. Environment (.env)")
    try:
        from src.env import load_env
        load_env()
        ok("load_env() ran")
    except Exception as e:
        fail("load_env()", str(e))
    for var in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_JWT_SECRET"):
        v = os.environ.get(var, "")
        if v:
            shown = v[:22] + "…" if len(v) > 24 else v
            if var in ("SUPABASE_JWT_SECRET", "DATABASE_URL", "SUPABASE_ANON_KEY"):
                shown = f"set (len {len(v)})"
            ok(f"{var}", shown)
        else:
            fail(f"{var}", "not set")

    # 2) DB connectivity -------------------------------------------------------
    section("2. Database connectivity")
    conn = None
    try:
        from src import db
        conn = db.connect()
        ver = conn.execute("SELECT version()").fetchone()[0]
        ok("db.connect()", ver.split(",")[0])
    except Exception as e:
        fail("db.connect()", str(e))
        _summary()
        return

    # 3) Schema ----------------------------------------------------------------
    section("3. Schema")
    expected_tables = ["users", "jobs", "user_jobs", "user_profiles", "user_settings",
                       "app_settings", "materials", "application_answers",
                       "notifications", "seen", "source_health", "runs", "errors",
                       "llm_usage"]
    try:
        present = {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public'").fetchall()}
        missing = [t for t in expected_tables if t not in present]
        if missing:
            fail("expected tables", "missing: " + ", ".join(missing))
        else:
            ok("all 14 tables present")
    except Exception as e:
        fail("table list", str(e))

    def cols(table):
        return {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=?", (table,)).fetchall()}

    try:
        jc = cols("jobs")
        if "status" in jc or "score" in jc:
            fail("jobs is pool-only", "still has judgement cols (status/score)")
        else:
            ok("jobs is pool-only", "no status/score on jobs")
        ujc = cols("user_jobs")
        need = {"user_id", "job_id", "status", "score", "served_at"}
        (ok if need <= ujc else fail)("user_jobs shape",
                                      "has " + ", ".join(sorted(need & ujc)))
        for t in ("materials", "application_answers", "notifications"):
            (ok if "user_id" in cols(t) else fail)(f"{t}.user_id present")
    except Exception as e:
        fail("column checks", str(e))

    # 4) Migrated data ---------------------------------------------------------
    section("4. Data")
    admin = None
    try:
        counts = {}
        for t in ("users", "jobs", "user_jobs", "materials", "application_answers",
                  "user_profiles", "user_settings", "app_settings", "notifications",
                  "seen", "source_health", "runs"):
            counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        ok("row counts", " ".join(f"{k}={v}" for k, v in counts.items()))
    except Exception as e:
        fail("row counts", str(e))
    try:
        row = conn.execute(
            "SELECT id, email, is_admin FROM users ORDER BY is_admin DESC NULLS LAST, "
            "created_at LIMIT 1").fetchone()
        if not row:
            fail("resolve a user", "no rows in public.users")
        else:
            admin = str(row[0])
            ok("primary user", f"{row[1]} (is_admin={row[2]})")
    except Exception as e:
        fail("resolve a user", str(e))

    if admin:
        try:
            pool = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            mine = conn.execute("SELECT COUNT(*) FROM user_jobs WHERE user_id=?",
                                (admin,)).fetchone()[0]
            detail = f"pool={pool}, your user_jobs={mine}"
            if mine == 0 and pool > 0:
                warn("your feed vs pool", detail + " (0 user_jobs — profile/migration?)")
            else:
                ok("your feed vs pool", detail)
        except Exception as e:
            fail("feed vs pool", str(e))
        try:
            p = conn.execute("SELECT profile FROM user_profiles WHERE user_id=?",
                             (admin,)).fetchone()
            if p and p[0]:
                keys = list(p[0].keys()) if isinstance(p[0], dict) else "present"
                ok("your profile migrated", f"keys: {keys}")
            else:
                warn("your profile", "no user_profiles row — scoring/prefilter will no-op")
        except Exception as e:
            fail("profile check", str(e))

    # 5) App import ------------------------------------------------------------
    section("5. Application loads")
    try:
        import src.api as api
        # This FastAPI keeps included routers as wrapper objects, so read the full
        # path list from the OpenAPI spec rather than app.routes.
        spec = api.app.openapi()
        routes = set(spec.get("paths", {}).keys())
        ok("import src.api", f"{len(routes)} routes registered")
        for want in ("/api/public-config", "/api/jobs/get-new", "/api/counts",
                     "/api/dashboard/user", "/api/jobs/{job_id}/materials"):
            (ok if want in routes else fail)(f"route {want}",
                                             "registered" if want in routes else "MISSING")
    except Exception as e:
        fail("import src.api", str(e))
        traceback.print_exc()

    # 6) Auth config -----------------------------------------------------------
    section("6. Auth config")
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if url:
        jwks_url = f"{url}/auth/v1/.well-known/jwks.json"
        try:
            data = json.loads(urllib.request.urlopen(jwks_url, timeout=10).read())
            kids = [(k.get("kid"), k.get("alg")) for k in data.get("keys", [])]
            ok("JWKS reachable", f"{len(kids)} key(s): {kids}")
        except Exception as e:
            warn("JWKS reachable", f"{type(e).__name__}: {e} (fine if you only use HS256)")
    try:
        from src.api import public_config
        pc = public_config()
        good = bool(pc.get("supabase_url")) and bool(pc.get("supabase_anon_key"))
        (ok if good else fail)("/api/public-config values",
                               "url + anon key present" if good else f"incomplete: {pc}")
    except Exception as e:
        fail("public_config()", str(e))

    if args.token:
        try:
            from src.auth import verify_token
            claims = verify_token(args.token)
            sub = claims.get("sub")
            match = conn.execute("SELECT email FROM users WHERE id=?", (sub,)).fetchone()
            ok("token verifies", f"sub={sub} alg-ok; user={'yes' if match else 'NOT in users!'}")
        except Exception as e:
            fail("token verify", str(e))

    # 7) Per-user module smoke (read-only) -------------------------------------
    section("7. Per-user endpoints (read-only, as your user)")
    if not admin:
        warn("module smoke", "skipped — no user resolved")
    else:
        from src import db as _db

        def call(name, fn):
            c = _db.connect()
            try:
                out = fn(c)
                ok(name, out if isinstance(out, str) else "ok")
            except Exception as e:
                fail(name, f"{type(e).__name__}: {e}")
            finally:
                c.close()

        from src.deps import _user_profile, _user_threshold
        call("deps._user_threshold", lambda c: f"threshold={_user_threshold(c, admin)}")
        call("deps._user_profile", lambda c: f"profile_keys={list(_user_profile(c, admin).keys())}")

        from src.routes import jobs as J
        call("jobs.counts", lambda c: json.dumps(J.counts(user_id=admin, conn=c)))
        call("jobs.stats", lambda c: "returned " + str(type(J.stats(user_id=admin, conn=c)).__name__))

        from src.routes import dashboard as D
        call("dashboard.user_dashboard",
             lambda c: "new_today=" + str(D.user_dashboard(user_id=admin, conn=c).get("new_today")))

        from src.routes import settings as S
        call("settings.get_settings", lambda c: json.dumps(S.get_settings(user_id=admin, conn=c)))

        from src import followups
        call("followups.summary", lambda c: json.dumps(followups.summary(c, admin)))

        from src.scoring import feedback
        call("feedback.stats", lambda c: json.dumps(feedback.stats(c, admin)))

        from src import health
        call("health.assess", lambda c: f"{len(health.assess(c))} boards")

    # 8) get-new candidates (read-only count) ----------------------------------
    section("8. Get-new candidates (read-only)")
    if admin:
        try:
            window = int(conn.execute(
                "SELECT value FROM app_settings WHERE key='new_job_window_days'"
            ).fetchone()[0] if conn.execute(
                "SELECT COUNT(*) FROM app_settings WHERE key='new_job_window_days'"
            ).fetchone()[0] else 5)
        except Exception:
            window = 5
        try:
            cand = conn.execute(
                "SELECT COUNT(*) FROM jobs j LEFT JOIN user_jobs uj "
                "ON uj.job_id=j.id AND uj.user_id=? "
                "WHERE uj.job_id IS NULL "
                "AND j.fetched_at >= now() - (? || ' days')::interval",
                (admin, window)).fetchone()[0]
            msg = f"{cand} unseen pool job(s) in the last {window} days"
            (ok if True else warn)("get-new candidate query", msg)
            if cand == 0:
                warn("candidates", "0 — 'Get new jobs' will find nothing until a fetch adds recent pool jobs")
        except Exception as e:
            fail("get-new candidate query", str(e))

    try:
        conn.close()
    except Exception:
        pass
    _summary()


def _summary():
    section("SUMMARY")
    p = sum(1 for r in RESULTS if r[0] == "PASS")
    w = sum(1 for r in RESULTS if r[0] == "WARN")
    f = sum(1 for r in RESULTS if r[0] == "FAIL")
    print(f"  {p} passed, {w} warnings, {f} failed")
    if f:
        print("\n  FAILURES:")
        for lvl, name, detail in RESULTS:
            if lvl == "FAIL":
                print(f"    - {name}: {detail}")
    if w:
        print("\n  WARNINGS:")
        for lvl, name, detail in RESULTS:
            if lvl == "WARN":
                print(f"    - {name}: {detail}")
    print("\nPaste this whole output back for analysis.")


if __name__ == "__main__":
    main()
