"""LLM rerank over the top-N candidates.

Returns a structured result per job: sub-scores (skills / seniority / domain),
a one-line rationale, and any flags. Combine into one composite score.
"""
# TODO: prompt the LLM with the profile + JD; parse structured JSON back.
