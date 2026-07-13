#!/usr/bin/env bash
# JobPilot repo cleanup — removes junk and dead files found in the audit.
# Nothing here is used by the running app; all of it was verified safe to delete.
set -e

# Stale duplicates committed by mistake. The real files live in frontend/ and as
# dotfiles — these root copies are old snapshots and a trap: editing them changes
# nothing, because StaticFiles serves frontend/.
git rm -f app.js index.html env.example gitignore gitignore.txt

# A 3-line TODO stub, imported by nothing.
git rm -f src/scoring/embed.py

git commit -m "Remove stale root duplicates and the dead embed stub"
