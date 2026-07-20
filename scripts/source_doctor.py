"""Why is this source returning nothing? — one live fetch per source, with the reason.

The Admin tab can only report the shape of the failure: "has never returned a job in 13
runs". It cannot say why, because the run that failed is long gone and all that was kept
was a count. This re-runs each source now and reports what actually happens — the config
it used, the error it raised, or the zero it returned — so "11 sources not working"
becomes eleven separate, answerable questions.

    python -m scripts.source_doctor              # every active source
    python -m scripts.source_doctor --broken     # only ones health calls broken
    python -m scripts.source_doctor Eluta CGI    # named sources only

Nothing is saved and nothing is scored; this only fetches.
"""
import argparse
import os
import pathlib
import sys
import time
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.modules.setdefault("ollama", types.ModuleType("ollama"))

from src.env import load_env                                   # noqa: E402
load_env()   # the API-key sources read os.environ, exactly as the app does at startup

from src import store                                          # noqa: E402
from src.adapters.base import get_adapter                       # noqa: E402
from src.config import load_companies                           # noqa: E402

#: Sources whose adapter needs a key, and the variables it looks for.
NEEDS_KEYS = {
    "adzuna": ("ADZUNA_APP_ID", "ADZUNA_APP_KEY"),
    "jsearch": ("JSEARCH_API_KEY",),
}

#: Adapters that scrape a page rather than call an API, so they need a URL.
NEEDS_URL = {"custom", "aggregator", "successfactors"}


def broken_names() -> set[str]:
    """What the Admin tab currently calls broken."""
    try:
        conn = store.connect()
        rows = conn.execute(
            "SELECT name FROM source_health WHERE zero_streak >= 3 OR error_streak >= 3"
        ).fetchall()
        conn.close()
        return {r[0] for r in rows}
    except Exception:
        return set()


def describe_config(c: dict) -> list[str]:
    """The settings that decide whether this source can work at all."""
    ats = (c.get("ats") or "").lower()
    notes = []

    for var in NEEDS_KEYS.get(ats, ()):
        notes.append(f"{var}={'set' if os.environ.get(var) else 'MISSING'}")

    # For JSearch, the host is the thing that actually goes wrong, and it was invisible:
    # the same key is valid at one host and a 401 at the other. Say which one it picked.
    if ats == "jsearch":
        key = (os.environ.get("JSEARCH_API_KEY") or "").strip()
        if key:
            from src.adapters.jsearch import _RAPIDAPI_KEY_SHAPE
            configured = (c.get("host") or "").lower()
            if configured:
                host = "RapidAPI" if configured == "rapidapi" else "OpenWeb Ninja"
                notes.append(f"host={host} (set in config)")
            else:
                rapid = bool(_RAPIDAPI_KEY_SHAPE.match(key))
                notes.append(
                    f"host={'RapidAPI' if rapid else 'OpenWeb Ninja'} (from key shape)")

    if ats in NEEDS_URL:
        url = c.get("careers_url") or c.get("base") or c.get("url")
        notes.append(f"careers_url={url or 'MISSING'}")
    elif c.get("identifier"):
        notes.append(f"identifier={c['identifier']}")

    if c.get("queries"):
        notes.append(f"queries={len(c['queries'])}")
    elif c.get("query"):
        notes.append(f"query={c['query']!r}")
    for key in ("where", "country", "category"):
        if c.get(key):
            notes.append(f"{key}={c[key]}")
    return notes


def verdict_for(count: int, err: str | None, c: dict) -> str:
    """The most likely cause, in one line."""
    ats = (c.get("ats") or "").lower()
    if err:
        return f"ERROR — {err[:150]}"
    if count:
        return f"OK — {count} jobs"

    missing = [v for v in NEEDS_KEYS.get(ats, ()) if not os.environ.get(v)]
    if missing:
        return f"0 jobs — {', '.join(missing)} not set in .env"
    if ats in NEEDS_URL and not (c.get("careers_url") or c.get("base") or c.get("url")):
        return "0 jobs — no careers_url configured; nothing was fetched"
    if c.get("queries") or c.get("query"):
        if ats == "jsearch" and not c.get("location"):
            return ("0 jobs — the API answered and matched nothing. This one wants the "
                    "place inside the query: add `location: Canada` (or a city) to "
                    "this source, which makes it ask for \"developer in Canada\" "
                    "rather than \"developer\".")
        return ("0 jobs — the API answered but matched nothing. Widen the query, or "
                "check `where`/`country` spelling.")
    if ats in NEEDS_URL:
        return ("0 jobs — the page loaded but no job links were recognised. The URL may "
                "be a landing page rather than the listing, or the list is JS-rendered.")
    return "0 jobs — the board answered and had nothing. Check the identifier/slug."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", help="only these sources")
    ap.add_argument("--broken", action="store_true", help="only ones health calls broken")
    args = ap.parse_args()

    try:
        all_sources = load_companies() or []
    except FileNotFoundError:
        print("No sources file found at config/companies-backup.yaml — nothing to test.")
        return
    except Exception as e:
        print(f"config/companies-backup.yaml could not be read: {e}")
        return

    companies = [c for c in all_sources if c.get("active", True)]
    if args.names:
        wanted = {n.lower() for n in args.names}
        companies = [c for c in companies
                     if any(w in (c.get("name", "").lower()) for w in wanted)]
    elif args.broken:
        names = broken_names()
        companies = [c for c in companies if c.get("name") in names]

    if not companies:
        print("No matching active sources.")
        return

    print(f"Testing {len(companies)} source(s). Nothing is saved.\n")
    summary = {"ok": 0, "empty": 0, "error": 0}

    for c in companies:
        name = c.get("name", "(unnamed)")
        print(f"── {name}   [{c.get('ats', '?')}]")
        cfg = describe_config(c)
        if cfg:
            print(f"     config: {'  '.join(cfg)}")

        started = time.time()
        count, err = 0, None
        try:
            count = len(get_adapter(c).fetch())
        except Exception as e:
            err = f"{type(e).__name__}: {e}"

        took = time.time() - started
        print(f"     {verdict_for(count, err, c)}   ({took:.1f}s)")
        print()
        summary["error" if err else ("ok" if count else "empty")] += 1

    print(f"{summary['ok']} working · {summary['empty']} returned nothing · "
          f"{summary['error']} errored")


if __name__ == "__main__":
    main()
