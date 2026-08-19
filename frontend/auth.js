/* JobPilot browser auth — Supabase JWT.
 *
 * Loaded before app.js so the fetch interceptor is in place before Alpine fires
 * its first request. Every /api/ call goes out with the current Supabase access
 * token in an Authorization header; the backend verifies it (src/auth.py) and
 * scopes the response to that user. There is no server-side password any more.
 *
 * Flow:
 *   1. install a window.fetch wrapper that awaits the session token for /api/ calls
 *   2. fetch /api/public-config (the one open endpoint) to learn the Supabase URL
 *      and anon key, and build the client
 *   3. no session  -> show the login overlay; on sign-in, reload so the app boots
 *      cleanly with a token in hand
 *      session      -> hide the overlay and let the app run
 */
(function () {
  "use strict";

  var originalFetch = window.fetch.bind(window);
  var client = null;
  var ready = null;              // resolves once the client + first session check are done

  // Run a DOM-touching callback now if <body> exists, else once it does. The auth
  // scripts sit in <head>, so overlay work can't assume document.body is present.
  function whenBody(fn) {
    if (document.body) { fn(); return; }
    document.addEventListener("DOMContentLoaded", fn);
  }

  // ── token plumbing ─────────────────────────────────────────────────────────

  async function currentToken() {
    if (ready) { await ready; }
    if (!client) { return null; }
    var res = await client.auth.getSession();
    var session = res && res.data ? res.data.session : null;
    return session ? session.access_token : null;
  }

  function urlOf(input) {
    if (typeof input === "string") { return input; }
    if (input && input.url) { return input.url; }
    return String(input);
  }

  // Same-origin API paths that must NOT carry a token (they bootstrap auth itself
  // or are public). Everything else under /api/ gets the Authorization header.
  function isOpenApi(path) {
    return path.indexOf("/api/public-config") !== -1;
  }
  function isApi(path) {
    // handle absolute and relative forms
    return path.indexOf("/api/") === 0 ||
           path.indexOf("/api/") === path.indexOf("//") + 2 ||
           /^https?:\/\/[^/]+\/api\//.test(path);
  }

  window.fetch = async function (input, init) {
    init = init || {};
    var path = urlOf(input);

    if (!isApi(path) || isOpenApi(path)) {
      return originalFetch(input, init);
    }

    var token = await currentToken();
    if (!token) {
      showLogin();
      // Reject so callers' .catch fallbacks kick in instead of hitting a 401 wall.
      throw new Error("not authenticated");
    }

    var headers = new Headers(init.headers || (typeof input !== "string" && input.headers) || {});
    headers.set("Authorization", "Bearer " + token);
    init.headers = headers;
    return originalFetch(input, init);
  };

  // ── bootstrap ──────────────────────────────────────────────────────────────

  async function init() {
    var cfg;
    try {
      cfg = await originalFetch("/api/public-config").then(function (r) { return r.json(); });
    } catch (e) {
      showFatal("Couldn't reach the server to load sign-in settings.");
      return;
    }
    if (!cfg.supabase_url || !cfg.supabase_anon_key) {
      showFatal("Sign-in isn't configured on the server (missing SUPABASE_URL / SUPABASE_ANON_KEY).");
      return;
    }
    if (!window.supabase || !window.supabase.createClient) {
      showFatal("Sign-in library failed to load.");
      return;
    }

    client = window.supabase.createClient(cfg.supabase_url, cfg.supabase_anon_key, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true }
    });

    var res = await client.auth.getSession();
    var session = res && res.data ? res.data.session : null;
    if (session) { hideLogin(); mountSignOut(); }
    else { showLogin(); }

    client.auth.onAuthStateChange(function (_event, s) {
      if (s) { hideLogin(); mountSignOut(); }
      else { showLogin(); }
    });
  }

  // ── login overlay ──────────────────────────────────────────────────────────

  var OVERLAY_ID = "jp-auth-overlay";

  function showLogin() {
    whenBody(_showLogin);
  }

  function _showLogin() {
    if (document.getElementById(OVERLAY_ID)) { return; }
    var wrap = document.createElement("div");
    wrap.id = OVERLAY_ID;
    wrap.setAttribute("style", [
      "position:fixed", "inset:0", "z-index:99999",
      "background:#0f1115", "color:#e7e7e7",
      "display:grid", "place-items:center",
      "font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif"
    ].join(";"));
    wrap.innerHTML =
      '<form id="jp-auth-form" style="background:#1a1d24;padding:2rem;border-radius:12px;width:min(90vw,340px);box-shadow:0 10px 40px rgba(0,0,0,.4)">' +
        '<h1 style="font-size:1.1rem;margin:0 0 1.25rem;display:flex;align-items:center;gap:.5rem">' +
          '<span style="color:#c9a227">●</span> JobPilot</h1>' +
        '<input id="jp-auth-email" type="email" placeholder="Email" autocomplete="username" autofocus ' +
          'style="width:100%;box-sizing:border-box;padding:.7rem;border-radius:8px;border:1px solid #333;background:#0f1115;color:#e7e7e7;margin-bottom:.7rem">' +
        '<input id="jp-auth-pass" type="password" placeholder="Password" autocomplete="current-password" ' +
          'style="width:100%;box-sizing:border-box;padding:.7rem;border-radius:8px;border:1px solid #333;background:#0f1115;color:#e7e7e7;margin-bottom:.9rem">' +
        '<button type="submit" style="width:100%;padding:.7rem;border:0;border-radius:8px;background:#c9a227;color:#0f1115;font-weight:600;cursor:pointer">Sign in</button>' +
        '<p id="jp-auth-msg" style="min-height:1.1em;margin:.8rem 0 0;font-size:.85rem;color:#e06c6c"></p>' +
      '</form>';
    document.body.appendChild(wrap);

    var form = document.getElementById("jp-auth-form");
    form.addEventListener("submit", async function (e) {
      e.preventDefault();
      var msg = document.getElementById("jp-auth-msg");
      var btn = form.querySelector("button");
      var email = document.getElementById("jp-auth-email").value.trim();
      var pass = document.getElementById("jp-auth-pass").value;
      msg.style.color = "#e06c6c"; msg.textContent = "";
      btn.disabled = true; btn.textContent = "Signing in…";
      try {
        var out = await client.auth.signInWithPassword({ email: email, password: pass });
        if (out.error) {
          msg.textContent = out.error.message || "Sign-in failed.";
          btn.disabled = false; btn.textContent = "Sign in";
          return;
        }
        msg.style.color = "#7bc47f"; msg.textContent = "Signed in — loading…";
        // Reload so Alpine re-initialises with a token available from the first call.
        window.location.reload();
      } catch (err) {
        msg.textContent = "Sign-in failed. Check your connection and try again.";
        btn.disabled = false; btn.textContent = "Sign in";
      }
    });
  }

  function hideLogin() {
    var el = document.getElementById(OVERLAY_ID);
    if (el) { el.remove(); }
  }

  function showFatal(text) {
    showLogin();
    var msg = document.getElementById("jp-auth-msg");
    if (msg) { msg.textContent = text; }
    var form = document.getElementById("jp-auth-form");
    if (form) {
      var btn = form.querySelector("button");
      if (btn) { btn.disabled = true; }
    }
  }

  // A small, unobtrusive sign-out control, only present while authenticated.
  function mountSignOut() {
    whenBody(_mountSignOut);
  }

  function _mountSignOut() {
    if (document.getElementById("jp-signout")) { return; }
    var b = document.createElement("button");
    b.id = "jp-signout";
    b.textContent = "Sign out";
    b.title = "Sign out of JobPilot";
    b.setAttribute("style", [
      "position:fixed", "bottom:12px", "left:12px", "z-index:9998",
      "padding:.35rem .7rem", "font-size:.75rem",
      "background:rgba(26,29,36,.9)", "color:#9aa0aa",
      "border:1px solid #333", "border-radius:8px", "cursor:pointer",
      "font-family:system-ui,sans-serif"
    ].join(";"));
    b.addEventListener("click", async function () {
      if (client) { await client.auth.signOut(); }
      window.location.reload();
    });
    document.body.appendChild(b);
  }

  // Expose a tiny surface for the app if it wants it.
  window.jobpilotAuth = {
    token: currentToken,
    signOut: async function () { if (client) { await client.auth.signOut(); } },
    user: async function () {
      if (ready) { await ready; }
      if (!client) { return null; }
      var res = await client.auth.getUser();
      return res && res.data ? res.data.user : null;
    }
  };

  // Kick off immediately (not on DOMContentLoaded) so `ready` is set before Alpine
  // can fire its first request. The interceptor above awaits `ready`; init() defers
  // any DOM work via whenBody().
  ready = init();
})();
