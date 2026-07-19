"""Imported jobs are location-filtered even without a description.

Alert emails always carry a location, so a non-Canada, non-remote job can be dropped at
import time rather than parked in the unscored tab. Scoring still needs a description;
this gate needs only the location.
"""
from unittest.mock import patch

from src import importers


class TestImportLocationGate:
    PROFILE = {"constraints": {"locations": ["remote", "toronto", "ontario", "canada"]}}

    def _import(self, jobs, db):
        with patch.object(importers, "load_profile", return_value=self.PROFILE), \
             patch("src.importers.recover_description", return_value=""):
            return importers.import_jobs(jobs)

    def test_us_job_without_description_is_dropped(self, db):
        stats = self._import(db=db, jobs=[
            {"title": "Dev", "company": "X", "location": "New York, NY",
             "apply_url": "http://e/1"},
        ])
        assert stats["dropped"] == 1
        assert stats["unscored"] == 0

    def test_canada_job_without_description_stays_unscored(self, db):
        stats = self._import(db=db, jobs=[
            {"title": "Dev", "company": "X", "location": "Toronto, ON, Canada",
             "apply_url": "http://e/2"},
        ])
        assert stats["unscored"] == 1
        assert stats["dropped"] == 0

    def test_remote_job_stays_unscored(self, db):
        stats = self._import(db=db, jobs=[
            {"title": "Dev", "company": "X", "location": "Remote",
             "remote": 1, "apply_url": "http://e/3"},
        ])
        assert stats["unscored"] == 1
        assert stats["dropped"] == 0

    def test_mixed_batch_keeps_only_relevant(self, db):
        stats = self._import(db=db, jobs=[
            {"title": "A", "company": "X", "location": "Toronto, Canada", "apply_url": "http://e/a"},
            {"title": "B", "company": "Y", "location": "London, UK", "apply_url": "http://e/b"},
            {"title": "C", "company": "Z", "location": "Remote", "remote": 1, "apply_url": "http://e/c"},
            {"title": "D", "company": "W", "location": "Austin, TX", "apply_url": "http://e/d"},
        ])
        assert stats["unscored"] == 2      # Toronto + Remote
        assert stats["dropped"] == 2       # London + Austin


class TestUnknownLocationIsKept:
    """Some employers' alerts name no location at all (Deloitte's, for one). Those
    postings sit on a Canadian careers site and are perfectly relevant — dropping them
    for a missing field would throw away good jobs silently."""
    PROFILE = {"constraints": {"locations": ["remote", "toronto", "ontario", "canada"]}}

    def _import(self, jobs, db):
        with patch.object(importers, "load_profile", return_value=self.PROFILE), \
             patch("src.importers.recover_description", return_value=""):
            return importers.import_jobs(jobs)

    def test_not_specified_location_is_not_dropped(self, db):
        stats = self._import(db=db, jobs=[
            {"title": "Junior Software Engineer", "company": "Deloitte",
             "location": "Not specified", "apply_url": "http://careers.deloitte.ca/job/x/1/"},
        ])
        assert stats["dropped"] == 0
        assert stats["unscored"] == 1

    def test_blank_location_is_not_dropped(self, db):
        stats = self._import(db=db, jobs=[
            {"title": "Dev", "company": "X", "location": "",
             "apply_url": "http://careers.example.com/job/y/2/"},
        ])
        assert stats["dropped"] == 0


class TestCanadianAbbreviationsAreRecognised:
    """Boards write "Mississauga, ON, CA" far more often than "Mississauga, Ontario".
    Matching only spelled-out words dropped every such posting — real Ontario jobs
    discarded over a formatting choice. None of the Canadian province codes collides
    with a US state code, so a match is unambiguous.
    """
    ALLOWED = ["remote", "toronto", "ontario", "canada"]

    def _keep(self, location, remote=0):
        from src.scoring.prefilter import _check_locations
        return _check_locations({"location": location, "remote": remote}, self.ALLOWED)

    def test_province_codes_are_canadian(self):
        for loc in ("Mississauga, ON, CA", "Scarborough, ON, CA, L4W0B4",
                    "Vancouver, BC, CA", "Montreal, QC", "Calgary, AB",
                    "Halifax, NS", "Winnipeg, MB"):
            assert self._keep(loc), f"{loc} should be kept"

    def test_us_states_are_not_canadian(self):
        for loc in ("New York, NY", "Austin, TX", "Seattle, WA"):
            assert not self._keep(loc), f"{loc} should be dropped"

    def test_california_is_not_canada(self):
        # "CA" here is California, not Canada — the province-code list excludes it.
        assert not self._keep("San Francisco, CA")

    def test_london_uk_is_not_ontario(self):
        # "London" contains the letters "on"; word boundaries stop a false match.
        assert not self._keep("London, UK")
        assert not self._keep("Berlin, Germany")
