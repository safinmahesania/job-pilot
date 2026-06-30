"""Maps raw adapter records into the common job schema + dedupe hash.

Common schema fields:
  id, source, source_url, title, company, location, remote_flag,
  salary_min, salary_max, description, requirements, posted_date,
  fetched_at, dedupe_hash
"""
# TODO: implement normalize(raw, company) -> dict and a dedupe_hash() helper.
