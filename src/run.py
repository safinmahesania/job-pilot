"""Phase 1 pipeline: fetch -> prefilter -> AI score -> store."""
from datetime import datetime
from src.config import load_companies, load_profile
from src.adapters.base import get_adapter
from src.normalize import normalize, is_valid
from src.scoring.prefilter import passes
from src.scoring.rerank import score_job
from src import store


def run():
    profile = load_profile()
    companies = load_companies()
    conn = store.connect()

    stats = {"fetched": 0, "seen": 0, "dropped": 0, "trashed": 0, "kept": 0, "errors": 0}
    seen_this_run = set()  # <- YEH LINE ADD KAR
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    for c in companies:
        if not c.get("active"):
            continue

        src_stat = {"fetched": 0, "kept": 0, "status": "ok", "error": None}
        try:
            raw_jobs = get_adapter(c).fetch()
        except Exception as e:
            print(f"[{c['name']}] fetch failed: {e}")
            stats["errors"] += 1
            src_stat["status"] = "error"
            src_stat["error"] = str(e)[:200]
            store.save_source_health(conn, c["name"], c.get("ats"), src_stat, now)
            continue

        for raw in raw_jobs:
            stats["fetched"] += 1
            src_stat["fetched"] += 1
            job = normalize(raw)
            h = job["dedupe_hash"]

            if store.already_seen(conn, h) or h in seen_this_run:
                stats["seen"] += 1
                continue
            seen_this_run.add(h)
            if not is_valid(job):
                store.mark_seen(conn, h, "dropped")
                stats["dropped"] += 1
                continue
            if not passes(job, profile):
                store.mark_seen(conn, h, "dropped")
                stats["dropped"] += 1
                continue

            result = score_job(job, profile)
            if result is None:
                stats["errors"] += 1
                continue

            # store ALL scored jobs (feed threshold se filter karega), kept/trashed mark
            job.update(score=result.overall, skills_score=result.skills_score,
                       seniority_score=result.seniority_score,
                       domain_score=result.domain_score,
                       rationale=result.rationale, flags=None)
            store.save_job(conn, job)

            threshold = int(store.get_setting(conn, "score_threshold", 70))
            if result.overall >= threshold:
                store.mark_seen(conn, h, "kept", result.overall)
                stats["kept"] += 1
                src_stat["kept"] += 1
            else:
                store.mark_seen(conn, h, "trashed", result.overall)
                stats["trashed"] += 1

        store.save_source_health(conn, c["name"], c.get("ats"), src_stat, now)

    conn.commit()
    conn.close()

    print("\n=== Run summary ===")
    for k, v in stats.items():
        print(f"  {k:10} {v}")


if __name__ == "__main__":
    run()