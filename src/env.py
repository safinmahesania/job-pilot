"""Load .env once, explicitly, at process start.

Modules used to each call load_dotenv() at import time. That worked, but it meant
importing llm or notify — for a test, say — reached out and read a .env from disk as a
side effect, which is how a developer's real password once ended up in the test suite.

Environment loading is a program-startup concern, not an import concern. The entry
points (the API, the CLI runner, the scheduler) call load_env() once, on purpose;
importing a module does nothing to the environment.
"""
from pathlib import Path

from dotenv import load_dotenv

# The .env sits next to the project, not wherever the process happened to be started
# from. load_dotenv() with no argument searches the current working directory and its
# parents — so launching the app from a different folder (an IDE with its own cwd, a
# shortcut, a subdirectory) would silently find no .env and every key would read as
# unset: Telegram "not configured", provider keys missing, all of it. Pinning the path
# to the repo root makes the load independent of where you ran it from.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

_loaded = False


def load_env() -> None:
    """Load .env into os.environ, at most once. Safe to call from every entry point."""
    global _loaded
    if not _loaded:
        # Explicit path first (the repo's own .env), then fall back to the default
        # search so a .env placed somewhere unusual still works.
        if _ENV_PATH.exists():
            load_dotenv(_ENV_PATH)
        else:
            load_dotenv()
        _loaded = True
