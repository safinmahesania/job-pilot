"""One logger for the whole app.

Diagnostics used to go to print(): no levels, so you could not quiet the noise or
raise it when chasing a bug; no timestamps, so "it failed" had no "when"; and no
single place to change any of that. This gives one configured logger the rest of the
code asks for by name.

Two rules keep the change honest:

  * This is for DIAGNOSTICS — the "[scheduler] loop error" line, the adapter that
    failed to fetch. It is not for program OUTPUT: `python -m src.run` printing its
    run summary, or `src.report` printing the job table, are the program talking to
    the person who ran it, and those stay as print().

  * The level comes from the JOBPILOT_LOG_LEVEL environment variable (INFO by
    default), so a deployment can turn it down to WARNING or up to DEBUG without a
    code change.

Usage:

    from src.logs import log
    log.info("fetched %d sources", n)
    log.warning("[notify] telegram failed: %s", e)
    log.exception("[scheduler] loop error")   # inside an except: includes traceback
"""
import logging
import os

_LEVEL = os.environ.get("JOBPILOT_LOG_LEVEL", "INFO").upper()

log = logging.getLogger("jobpilot")

if not log.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(handler)
    log.setLevel(getattr(logging, _LEVEL, logging.INFO))
    # Don't double-emit through the root logger's default handler.
    log.propagate = False
