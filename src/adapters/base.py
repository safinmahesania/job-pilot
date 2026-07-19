"""Shared adapter interface + factory that routes by ATS type."""
from abc import ABC, abstractmethod
import re

#: Query-string parameters that carry a credential. httpx puts the full request URL in
#: the exception it raises for a bad status, so logging that exception verbatim writes
#: your API keys into the log — and logs get pasted into chats and bug reports. This is
#: not hypothetical: it happened, with a live key, the first time a source was diagnosed.
_SECRET_PARAMS = ("app_key", "app_id", "api_key", "apikey", "key", "token",
                  "access_token", "secret", "password")
_SECRET_RE = re.compile(
    r"(?i)\b(" + "|".join(_SECRET_PARAMS) + r")=([^&\s'\"]+)"
)


def redact(text) -> str:
    """Error text with any credential in it replaced, safe to log or show."""
    return _SECRET_RE.sub(r"\1=***", str(text))

class SourceAdapter(ABC):
    def __init__(self, company: dict):
        self.company = company
        self.name = company["name"]
        self.identifier = company.get("identifier")

    @abstractmethod
    def fetch(self) -> list[dict]:
        """Return a list of raw job dicts from this source."""
        ...


def get_adapter(company: dict) -> SourceAdapter:
    """Map a company's `ats` field to the right adapter."""
    from .greenhouse import GreenhouseAdapter
    from .lever import LeverAdapter
    from .themuse import TheMuseAdapter
    from .remotive import RemotiveAdapter
    from .workday import WorkdayAdapter
    from .remoteok import RemoteOKAdapter
    from .weworkremotely import WeWorkRemotelyAdapter
    from .jobspresso import JobspressoAdapter
    from .oracle import OracleAdapter
    from .phenom import PhenomAdapter
    from .ashby import AshbyAdapter
    from .generic import GenericCareersAdapter
    from .smartrecruiters import SmartRecruitersAdapter
    from .workable import WorkableAdapter
    from .jsearch import JSearchAdapter
    from .adzuna import AdzunaAdapter

    ats = company.get("ats")
    registry = {
        "greenhouse": GreenhouseAdapter,
        "lever": LeverAdapter,
        "themuse": TheMuseAdapter,
        "remotive": RemotiveAdapter,
        "workday": WorkdayAdapter,
        "remoteok": RemoteOKAdapter,
        "weworkremotely": WeWorkRemotelyAdapter,
        "jobspresso": JobspressoAdapter,
        "oracle": OracleAdapter,
        "phenom": PhenomAdapter,
        "ashby": AshbyAdapter,
        "smartrecruiters": SmartRecruitersAdapter,
        "workable": WorkableAdapter,
        "jsearch": JSearchAdapter,
        "adzuna": AdzunaAdapter,
        # HTML-scrape sources with no JSON API — all served by one generic adapter that
        # pulls job links out of a careers page. Best-effort by nature (see generic.py).
        "custom": GenericCareersAdapter,
        "aggregator": GenericCareersAdapter,
        "successfactors": GenericCareersAdapter,
    }
    if ats not in registry:
        raise ValueError(f"No adapter for ats='{ats}' (company: {company['name']})")
    return registry[ats](company)


# The ats values the app actually knows how to fetch. Kept next to the registry so
# the two cannot drift: this is what the "add a source" endpoint validates against,
# so a typo like ats="greehnouse" is caught at the form instead of surfacing later as
# a fetch error against a source that can never work.
KNOWN_ATS = frozenset({
    "greenhouse", "lever", "themuse", "remotive", "workday", "remoteok",
    "weworkremotely", "jobspresso", "oracle", "phenom", "ashby",
    "smartrecruiters", "workable", "jsearch", "adzuna",
    "custom", "aggregator", "successfactors",
})
