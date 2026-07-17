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
