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
import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def gated_client(monkeypatch, tmp_path):
    """The app with a password set and a real (empty) database."""
    db = str(tmp_path / "gated.db")
    # Close the setup connection before yielding. On Windows an open handle makes the
    # file unremovable, so a leaked connection surfaces as a PermissionError at
    # teardown — a real leak, hidden on Linux where an open file can still be unlinked.
    conn = sqlite3.connect(db)
    conn.executescript(open("data/schema.sql", encoding="utf-8").read())
    conn.commit()
    conn.close()

    monkeypatch.setenv("JOBPILOT_PASSWORD", "correct-horse")
    monkeypatch.setattr("src.paths.DB_PATH", db)

    # No importlib.reload: the gate reads the password per request, so setting the env
    # var is enough. Reloading would re-run load_dotenv() in notify/llm and drag a
    # developer's real .env back into the test.
    import src.api as api
    import src.store as store
    store.DB = db
    import src.deps as deps
    deps.DB = db

    yield api


@pytest.fixture
def open_client(monkeypatch, tmp_path):
    """The app with NO password — local development."""
    db = str(tmp_path / "open.db")
    conn = sqlite3.connect(db)
    conn.executescript(open("data/schema.sql", encoding="utf-8").read())
    conn.commit()
    conn.close()

    monkeypatch.delenv("JOBPILOT_PASSWORD", raising=False)
    monkeypatch.setattr("src.paths.DB_PATH", db)

    import src.api as api
    import src.store as store
    store.DB = db
    import src.deps as deps
    deps.DB = db

    yield api


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


class TestNoNetworkFactIsTrusted:
    """The audit found the first version of this gate backwards.

    It trusted "the request arrived on localhost" as proof the request was local. But
    cloudflared connects to the app ON localhost — every tunnel request arrives from
    127.0.0.1 — so the rule was really "trust anything through the tunnel", the exact
    opposite of the point. Stripping one forwarded header opened the gate onto
    /api/maint/reset; it was demonstrated with the database wipeable, no password.

    Nothing about the connection is trusted now — not the IP, not the absence of a
    header. Only the password.
    """

    def test_a_localhost_request_without_the_key_is_refused(self, gated_client):
        """The exact shape of the bypass: a request that looks local, no credential.
        It used to return 200. It must be 401."""
        client = TestClient(gated_client.app)

        assert client.get("/api/errors").status_code == 401

    def test_the_reset_endpoint_cannot_be_reached_by_looking_local(
            self, gated_client):
        client = TestClient(gated_client.app)

        assert client.post("/api/maint/reset").status_code == 401

    def test_forging_a_forwarded_header_does_not_help(self, gated_client):
        client = TestClient(gated_client.app)

        assert client.get("/api/errors",
                          headers={"cf-connecting-ip": "1.2.3.4"}).status_code == 401

    def test_the_extension_authenticates_with_the_header(self, gated_client):
        """It cannot log in through the browser form, so it carries the password as
        x-jobpilot-key on every call. That, not its IP, is what gets it in."""
        client = TestClient(gated_client.app)

        r = client.get("/api/counts", headers={"x-jobpilot-key": "correct-horse"})

        assert r.status_code == 200


class TestCORSDoesNotHandOutCredentials:
    """The extension calls the API cross-origin. The CORS policy must let it in
    WITHOUT ever letting a cross-origin caller carry the session cookie — that is the
    difference between "the extension can use the API" and "any page can act as you".
    """

    def test_credentials_are_never_allowed_cross_origin(self):
        """allow_credentials=False: the cookie is same-origin only. A cross-origin
        request authenticates with the explicit header or not at all."""
        import src.api as api

        cors = next(m for m in api.app.user_middleware
                    if "CORS" in m.cls.__name__)

        assert cors.kwargs.get("allow_credentials") is False

    def test_methods_and_headers_are_named_not_wildcarded(self):
        """A wildcard invites any extension to send anything. The app needs a fixed,
        small set."""
        import src.api as api

        cors = next(m for m in api.app.user_middleware
                    if "CORS" in m.cls.__name__)

        assert "*" not in cors.kwargs.get("allow_methods", [])
        assert "*" not in cors.kwargs.get("allow_headers", [])