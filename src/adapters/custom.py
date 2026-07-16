"""Superseded by the generic HTML adapter.

`ats: custom` used to raise NotImplementedError here because bespoke career pages have
no JSON API. They are now handled by src/adapters/generic.py (GenericCareersAdapter),
which scrapes job links out of the page — best-effort, but real. The registry routes
`custom`, `aggregator`, and `successfactors` all there.

This file is kept only so the import path doesn't disappear from under anything that
still references it; the class below is no longer registered anywhere.
"""
from src.adapters.generic import GenericCareersAdapter


# Backwards-compatible alias — anything importing CustomAdapter gets the working one.
CustomAdapter = GenericCareersAdapter
