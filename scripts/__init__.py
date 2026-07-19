"""Command-line diagnostics.

Importing this package makes stdout UTF-8. Every script here draws rules and headings
with box characters, and on Windows that works in the console and then dies the moment
the output is sent anywhere:

    python -m scripts.source_doctor > doctor.txt
    UnicodeEncodeError: 'charmap' codec can't encode characters in position 0-1

Python uses the console's encoding when attached to one, and the legacy locale encoding
(cp1252 on a Canadian/US Windows) when redirected. cp1252 has no box characters, so
writing the report to a file crashes the script that produced it — exactly when you are
trying to capture output to send to someone.

This runs on import, before any script's own code, because `python -m scripts.X`
imports the package first. Saving a report is a normal thing to do and should not have
to be worked around.
"""
import sys

for _stream in (sys.stdout, sys.stderr):
    # Not every stdout is a real file: under pytest it is a capture object with no
    # reconfigure(). Guard rather than assume.
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # A detached or already-closed stream. Nothing to fix, and refusing to
            # import the package over it would be worse than plain output.
            pass
