"""Shared adapter interface + factory that routes by ATS type."""
from abc import ABC, abstractmethod

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
    }
    if ats not in registry:
        raise ValueError(f"No adapter for ats='{ats}' (company: {company['name']})")
    return registry[ats](company)