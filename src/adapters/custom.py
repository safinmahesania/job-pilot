"""Placeholder for a Playwright-based scraper for custom career pages.

This is not implemented. It exists so that `ats: custom` in the registry fails with a
clear message instead of a confusing one — and so the intent is recorded in one place.

A custom page has no JSON API to hit, so scraping it means driving a headless browser
with per-company selectors. That is a real dependency (Playwright, a browser binary)
and a fragile one (selectors rot whenever the page is redesigned), so it is left out
until a page actually needs it. When one does, the `scrape` selectors already have a
home in the registry; this is where the code to use them goes.
"""
from src.adapters.base import SourceAdapter


class CustomAdapter(SourceAdapter):
    def fetch(self):
        raise NotImplementedError(
            "The 'custom' adapter (Playwright scraping of a bespoke career page) is "
            "not implemented yet. Use a supported ats (greenhouse, lever, ashby, "
            "workday, ...) or open an issue describing the page you need.")
