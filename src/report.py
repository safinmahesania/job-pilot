"""Show top kept jobs, ranked by score — the Phase 1 result."""
import sqlite3

from src.paths import DB_PATH as DB


def top_jobs(limit: int = 10):
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        """SELECT score, title, company, location, rationale, apply_url
           FROM jobs ORDER BY score DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()

    if not rows:
        print("No kept jobs yet. Run `python -m src.run` with a matching company.")
        return

    print(f"\n=== Top {len(rows)} matches ===\n")
    for i, (score, title, company, location, rationale, apply_url) in enumerate(rows, 1):
        print(f"{i}. [{score}] {title} — {company}")
        print(f"   {location or 'N/A'}")
        print(f"   {rationale}")
        print(f"   Apply: {apply_url}\n")


if __name__ == "__main__":
    top_jobs()