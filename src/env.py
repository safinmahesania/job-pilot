"""Load .env once, explicitly, at process start.

Modules used to each call load_dotenv() at import time. That worked, but it meant
importing llm or notify — for a test, say — reached out and read a .env from disk as a
side effect, which is how a developer's real password once ended up in the test suite.

Environment loading is a program-startup concern, not an import concern. The entry
points (the API, the CLI runner, the scheduler) call load_env() once, on purpose;
importing a module does nothing to the environment.
"""
from dotenv import load_dotenv

_loaded = False


def load_env() -> None:
    """Load .env into os.environ, at most once. Safe to call from every entry point."""
    global _loaded
    if not _loaded:
        load_dotenv()
        _loaded = True
