"""The lock on the door.

This app has a person's phone number and home address in it, and a reset endpoint
that empties the database with no confirmation. The moment it is on a public URL,
the URL is not a secret — bots sweep the whole internet — so the app itself has to
be able to say no.

Two properties matter, and a regression in either is a leak:

  - With no password set, the gate is OFF. Local development on localhost is
    unchanged, or the whole project becomes annoying to work on and someone turns
    the gate off the wrong way.
  - With a password set, a request arriving over the tunnel must log in, but a
    request from localhost — you, or the browser extension on the same machine —
    must not, or the extension breaks the day the app goes online.
"""
import importlib
import os
import sqlite3
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def gated_client(monkeypatch):
    """The app with a password set and a real (empty) database."""
    db = tempfile.mktemp(suffix=".db")
    sqlite3.connect(db).executescript(
        open("data/schema.sql", encoding="utf-8").read())

    monkeypatch.setenv("JOBPILOT_PASSWORD", "correct-horse")
    monkeypatch.setattr("src.paths.DB_PATH", db)

    import src.api as api
    importlib.reload(api)
    api.DB = db
    import src.store as store
    store.DB = db

    yield api
    os.unlink(db)


@pytest.fixture
def open_client(monkeypatch):
    """The app with NO password — local development."""
    db = tempfile.mktemp(suffix=".db")
    sqlite3.connect(db).executescript(
        open("data/schema.sql", encoding="utf-8").read())

    monkeypatch.delenv("JOBPILOT_PASSWORD", raising=False)
    monkeypatch.setattr("src.paths.DB_PATH", db)

    import src.api as api
    importlib.reload(api)
    api.DB = db
    import src.store as store
    store.DB = db

    yield api
    os.unlink(db)


TUNNEL = {"cf-connecting-ip": "8.8.8.8"}      # a request that came in over the tunnel


class TestWithNoPasswordTheGateIsOpen:
    def test_local_development_is_unchanged(self, open_client):
        client = TestClient(open_client.app)

        assert client.get("/api/counts").status_code == 200

    def test_even_a_tunnel_request_passes_when_no_password_is_set(self, open_client):
        """No password means no gate, for anyone. Setting the password is the deploy
        step; forgetting it is the mistake this cannot prevent, only the docs can."""
        client = TestClient(open_client.app)

        assert client.get("/api/counts", headers=TUNNEL).status_code == 200


class TestWithAPasswordTheTunnelMustLogIn:
    def test_a_tunnel_request_without_a_cookie_is_refused(self, gated_client):
        client = TestClient(gated_client.app)

        assert client.get("/api/counts", headers=TUNNEL).status_code == 401

    def test_the_wrong_password_does_not_get_in(self, gated_client):
        client = TestClient(gated_client.app)

        r = client.post("/api/login", data={"password": "hunter2"})

        assert r.status_code == 401

    def test_the_right_password_sets_a_secure_cookie(self, gated_client):
        client = TestClient(gated_client.app, follow_redirects=False)

        r = client.post("/api/login", data={"password": "correct-horse"})

        assert r.status_code == 303
        cookie = r.headers.get("set-cookie", "").lower()
        assert "jp_auth" in cookie
        assert "httponly" in cookie
        assert "secure" in cookie

    def test_a_tunnel_request_with_the_cookie_gets_in(self, gated_client):
        client = TestClient(gated_client.app)
        client.cookies.set("jp_auth", "correct-horse")

        assert client.get("/api/counts", headers=TUNNEL).status_code == 200

    def test_the_reset_endpoint_is_behind_the_gate(self, gated_client):
        """The specific thing that must never be reachable unauthenticated."""
        client = TestClient(gated_client.app)

        r = client.post("/api/maint/reset", headers=TUNNEL)

        assert r.status_code == 401


class TestLocalhostIsTrustedSoTheExtensionKeepsWorking:
    def test_localhost_bypasses_the_gate_even_when_a_password_is_set(
            self, gated_client):
        """The extension calls the API from the same machine. If the gate stopped it,
        going online would silently break autofill."""
        client = TestClient(gated_client.app)      # arrives as a local client

        assert client.get("/api/counts").status_code == 200

    def test_but_a_forwarded_header_from_localhost_is_still_challenged(
            self, gated_client):
        """Defence in depth: if something local is proxying tunnel traffic, the
        forwarded header gives it away and the trust does not apply."""
        client = TestClient(gated_client.app)

        r = client.get("/api/counts", headers={"x-forwarded-for": "8.8.8.8"})

        assert r.status_code == 401
