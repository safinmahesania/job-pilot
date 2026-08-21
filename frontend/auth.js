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
 *   3. no session  -> show the auth screen (sign in / create account / forgot /
 *      verify / set-new-password); on sign-in, reload so the app boots with a token
 *      session      -> hide the auth screen and let the app run
 */
(function () {
  "use strict";

  var originalFetch = window.fetch.bind(window);
  var client = null;
  var ready = null;              // resolves once the client + first session check are done

  function whenBody(fn) {
    if (document.body) { fn(); return; }
    document.addEventListener("DOMContentLoaded", fn);
  }

  // ── token plumbing (unchanged) ───────────────────────────────────────────────
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
  function isOpenApi(path) { return path.indexOf("/api/public-config") !== -1; }
  function isApi(path) {
    return path.indexOf("/api/") === 0 ||
           path.indexOf("/api/") === path.indexOf("//") + 2 ||
           /^https?:\/\/[^/]+\/api\//.test(path);
  }
  window.fetch = async function (input, init) {
    init = init || {};
    var path = urlOf(input);
    if (!isApi(path) || isOpenApi(path)) { return originalFetch(input, init); }
    var token = await currentToken();
    if (!token) { showAuth(); throw new Error("not authenticated"); }
    var headers = new Headers(init.headers || (typeof input !== "string" && input.headers) || {});
    headers.set("Authorization", "Bearer " + token);
    init.headers = headers;
    return originalFetch(input, init);
  };

  // ── bootstrap ────────────────────────────────────────────────────────────────
  async function init() {
    var cfg;
    try {
      cfg = await originalFetch("/api/public-config").then(function (r) { return r.json(); });
    } catch (e) { showFatal("Couldn't reach the server to load sign-in settings."); return; }
    if (!cfg.supabase_url || !cfg.supabase_anon_key) {
      showFatal("Sign-in isn't configured on the server (missing SUPABASE_URL / SUPABASE_ANON_KEY)."); return;
    }
    if (!window.supabase || !window.supabase.createClient) { showFatal("Sign-in library failed to load."); return; }

    client = window.supabase.createClient(cfg.supabase_url, cfg.supabase_anon_key, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true }
    });

    var res = await client.auth.getSession();
    var session = res && res.data ? res.data.session : null;
    if (session) { hideAuth(); mountSignOut(); } else { showAuth(); }

    client.auth.onAuthStateChange(function (event, s) {
      // Arriving from a reset link: keep the overlay up and collect a new password.
      if (event === "PASSWORD_RECOVERY") { showAuth(); showScreen("new"); return; }
      if (s) { hideAuth(); mountSignOut(); } else { showAuth(); }
    });
  }

  // ── auth overlay (soft-rounded) ───────────────────────────────────────────────
  var OVERLAY_ID = "jp-auth-overlay";

  function injectStyles() {
    if (document.getElementById("jp-auth-css")) { return; }
    var css = document.createElement("style");
    css.id = "jp-auth-css";
    css.textContent = [
      "#jp-auth-overlay{position:fixed;inset:0;z-index:99999;overflow:auto;",
        "font-family:'Inter',system-ui,-apple-system,sans-serif;color:#1A1A19;",
        "display:flex;align-items:center;justify-content:center;padding:40px 20px;",
        "background:#FCF6EA;",
        "background-image:radial-gradient(58% 46% at 86% 12%,#F7E9CE 0%,transparent 60%),radial-gradient(50% 42% at 8% 92%,#F5EAD3 0%,transparent 60%)}",
      "#jp-auth-overlay *{box-sizing:border-box}",
      ".jpa-card{width:100%;max-width:412px;background:#fff;border-radius:28px;padding:38px 36px 30px;box-shadow:0 24px 56px rgba(150,110,30,.13)}",
      ".jpa-head{text-align:center;margin-bottom:26px}",
      ".jpa-logo{width:60px;height:60px;margin:0 auto 16px;display:block;border-radius:18px;box-shadow:0 12px 24px rgba(169,109,20,.26)}",
      ".jpa-ic{width:60px;height:60px;border-radius:18px;background:#F6EEDD;color:#A96D14;display:flex;align-items:center;justify-content:center;font-size:30px;margin:0 auto 16px}",
      ".jpa-h{font-size:24px;font-weight:650;letter-spacing:-.02em;margin:0 0 6px}",
      ".jpa-lead{font-size:14.5px;color:#8A8985;margin:0;line-height:1.5}",
      ".jpa-lead b{color:#52514E;font-weight:600}",
      ".jpa-lbl{display:block;font-size:12.5px;font-weight:500;color:#52514E;margin:0 0 6px}",
      ".jpa-field{position:relative;margin-bottom:14px}",
      ".jpa-field>i{position:absolute;left:15px;top:50%;transform:translateY(-50%);font-size:18px;color:#8A8985;pointer-events:none}",
      ".jpa-inp{width:100%;height:50px;border:1.5px solid #F0EADD;border-radius:15px;padding:0 44px;font-family:inherit;font-size:14px;color:#1A1A19;background:#FCFAF5;outline:none;transition:.14s}",
      ".jpa-inp::placeholder{color:#BBB6A9}",
      ".jpa-inp:hover{border-color:#E6DDC9}",
      ".jpa-inp:focus{border-color:#A96D14;background:#fff;box-shadow:0 0 0 4px rgba(169,109,20,.20)}",
      ".jpa-eye{position:absolute;right:14px;top:50%;transform:translateY(-50%);border:0;background:none;color:#8A8985;cursor:pointer;font-size:18px;padding:4px;line-height:0}",
      ".jpa-eye:hover{color:#52514E}",
      ".jpa-rowend{display:flex;justify-content:flex-end;margin:-4px 0 16px}",
      ".jpa-cta{width:100%;height:52px;border:0;border-radius:16px;background:#A96D14;color:#fff;font-family:inherit;font-size:15px;font-weight:600;cursor:pointer;transition:.14s;display:flex;align-items:center;justify-content:center;gap:8px}",
      ".jpa-cta:hover{background:#7A4E0C}.jpa-cta:active{transform:scale(.99)}",
      ".jpa-cta:disabled{opacity:.7;cursor:default}",
      ".jpa-sso{width:100%;height:52px;border:1.5px solid #F0EADD;border-radius:16px;background:#fff;font-family:inherit;font-size:14px;font-weight:500;color:#1A1A19;cursor:pointer;transition:.14s;display:flex;align-items:center;justify-content:center;gap:10px}",
      ".jpa-sso:hover{background:#FCFAF5;border-color:#E6DDC9}",
      ".jpa-ghost{width:100%;height:48px;border:0;background:none;font-family:inherit;font-size:14px;font-weight:500;color:#7A4E0C;cursor:pointer;border-radius:14px;transition:.14s}",
      ".jpa-ghost:hover{background:#FAF3E6}",
      ".jpa-or{display:flex;align-items:center;gap:12px;margin:18px 0;color:#8A8985;font-size:12px}",
      ".jpa-or:before,.jpa-or:after{content:'';flex:1;height:1px;background:#F0E9DA}",
      ".jpa-foot{font-size:13px;color:#8A8985;text-align:center;margin-top:20px}",
      ".jpa-lk{color:#7A4E0C;font-weight:500;text-decoration:none;cursor:pointer}.jpa-lk:hover{text-decoration:underline}",
      ".jpa-back{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:#8A8985;text-decoration:none;margin-top:18px;justify-content:center;width:100%;cursor:pointer}",
      ".jpa-back:hover{color:#52514E}",
      ".jpa-legal{font-size:11.5px;color:#8A8985;line-height:1.5;text-align:center;margin-top:16px}",
      ".jpa-msg{min-height:1.1em;margin:12px 0 0;font-size:13px;line-height:1.4;text-align:center;color:#DC5B4B}",
      ".jpa-msg.ok{color:#1F9D6B}",
      ".jpa-g{font-size:18px;color:#A96D14}",
      ".jpa-hide{display:none!important}"
    ].join("");
    document.head.appendChild(css);
  }

  var LOGO = '<svg class="jpa-logo" viewBox="0 0 48 48" aria-hidden="true"><rect width="48" height="48" rx="12" fill="#A96D14"/><circle cx="24" cy="24" r="14" fill="none" stroke="#fff" stroke-width="1.3" opacity="0.9"/><path d="M24 10 L26.6 24 L24 24 Z" fill="#fff"/><path d="M24 10 L24 24 L21.4 24 Z" fill="#F0DCB4"/><path d="M24 38 L26.6 24 L24 24 Z" fill="#26375C"/><path d="M24 38 L24 24 L21.4 24 Z" fill="#1F2D4D"/><circle cx="24" cy="24" r="1.8" fill="#fff"/></svg>';

  function field(id, icon, type, ph, ac, eye) {
    return '<div class="jpa-field"><i class="ti ' + icon + '"></i>' +
      '<input id="' + id + '" class="jpa-inp' + (eye ? ' jpa-pw' : '') + '" type="' + type + '" placeholder="' + ph + '" autocomplete="' + ac + '">' +
      (eye ? '<button class="jpa-eye" type="button" data-eye><i class="ti ti-eye"></i></button>' : '') + '</div>';
  }

  function screensHTML() {
    return '' +
      // sign in
      '<form class="jpa-card jpa-screen" id="jpa-in">' +
        '<div class="jpa-head">' + LOGO + '<h1 class="jpa-h">Welcome back</h1><p class="jpa-lead">Sign in to your feed.</p></div>' +
        '<label class="jpa-lbl">Email</label>' + field("jpa-in-email", "ti-mail", "email", "name@email.com", "email") +
        '<label class="jpa-lbl">Password</label>' + field("jpa-in-pass", "ti-lock", "password", "Your password", "current-password", true) +
        '<div class="jpa-rowend"><a class="jpa-lk" data-go="fp">Forgot password?</a></div>' +
        '<button type="submit" class="jpa-cta">Sign in <i class="ti ti-arrow-right" style="font-size:18px"></i></button>' +
        '<div class="jpa-or">or</div>' +
        '<button type="button" class="jpa-sso" data-google><i class="ti ti-brand-google jpa-g"></i> Continue with Google</button>' +
        '<p class="jpa-msg" id="jpa-in-msg"></p>' +
        '<p class="jpa-foot">New to JobPilot? <a class="jpa-lk" data-go="up">Create an account</a></p>' +
      '</form>' +
      // sign up
      '<form class="jpa-card jpa-screen jpa-hide" id="jpa-up">' +
        '<div class="jpa-head">' + LOGO + '<h1 class="jpa-h">Create your account</h1><p class="jpa-lead">Let\u2019s find the good ones together.</p></div>' +
        '<label class="jpa-lbl">Name</label>' + field("jpa-up-name", "ti-user", "text", "Your name", "name") +
        '<label class="jpa-lbl">Email</label>' + field("jpa-up-email", "ti-mail", "email", "name@email.com", "email") +
        '<label class="jpa-lbl">Password</label>' + field("jpa-up-pass", "ti-lock", "password", "At least 8 characters", "new-password", true) +
        '<button type="submit" class="jpa-cta" style="margin-top:6px">Create account</button>' +
        '<div class="jpa-or">or</div>' +
        '<button type="button" class="jpa-sso" data-google><i class="ti ti-brand-google jpa-g"></i> Continue with Google</button>' +
        '<p class="jpa-msg" id="jpa-up-msg"></p>' +
        '<p class="jpa-legal">By continuing you agree to the <a class="jpa-lk" href="/terms" target="_blank">Terms</a> and <a class="jpa-lk" href="/privacy" target="_blank">Privacy policy</a>.</p>' +
        '<p class="jpa-foot" style="margin-top:12px">Already have an account? <a class="jpa-lk" data-go="in">Sign in</a></p>' +
      '</form>' +
      // forgot
      '<form class="jpa-card jpa-screen jpa-hide" id="jpa-fp">' +
        '<div class="jpa-head"><div class="jpa-ic"><i class="ti ti-key"></i></div><h1 class="jpa-h">Reset your password</h1><p class="jpa-lead">Enter your email and we\u2019ll send a link to set a new one.</p></div>' +
        '<label class="jpa-lbl">Email</label>' + field("jpa-fp-email", "ti-mail", "email", "name@email.com", "email") +
        '<button type="submit" class="jpa-cta" style="margin-top:4px">Send reset link</button>' +
        '<p class="jpa-msg" id="jpa-fp-msg"></p>' +
        '<a class="jpa-back" data-go="in"><i class="ti ti-arrow-left" style="font-size:16px"></i> Back to sign in</a>' +
      '</form>' +
      // sent / verify (shared)
      '<div class="jpa-card jpa-screen jpa-hide" id="jpa-sent">' +
        '<div class="jpa-head"><div class="jpa-ic"><i class="ti ti-mail-check"></i></div><h1 class="jpa-h" id="jpa-sent-h">Check your email</h1><p class="jpa-lead" id="jpa-sent-lead"></p></div>' +
        '<button type="button" class="jpa-ghost" data-resend>Resend link</button>' +
        '<a class="jpa-back" data-go="in"><i class="ti ti-arrow-left" style="font-size:16px"></i> Back to sign in</a>' +
      '</div>' +
      // set new password (recovery)
      '<form class="jpa-card jpa-screen jpa-hide" id="jpa-new">' +
        '<div class="jpa-head"><div class="jpa-ic"><i class="ti ti-lock-check"></i></div><h1 class="jpa-h">Set a new password</h1><p class="jpa-lead">Choose a password you\u2019ll remember.</p></div>' +
        '<label class="jpa-lbl">New password</label>' + field("jpa-new-pass", "ti-lock", "password", "At least 8 characters", "new-password", true) +
        '<button type="submit" class="jpa-cta" style="margin-top:4px">Update password</button>' +
        '<p class="jpa-msg" id="jpa-new-msg"></p>' +
      '</form>';
  }

  var _lastEmail = "", _lastMode = "reset";

  function showAuth() { whenBody(_showAuth); }

  function _showAuth() {
    if (document.getElementById(OVERLAY_ID)) { return; }
    injectStyles();
    var wrap = document.createElement("div");
    wrap.id = OVERLAY_ID;
    wrap.innerHTML = screensHTML();
    document.body.appendChild(wrap);
    wire(wrap);
    // Open the screen the landing page asked for (/app#signup -> create account).
    var h = (window.location.hash || "").toLowerCase();
    showScreen((h.indexOf("signup") !== -1 || h.indexOf("register") !== -1) ? "up" : "in");
  }

  function showScreen(name) {
    var o = document.getElementById(OVERLAY_ID);
    if (!o) { return; }
    ["in", "up", "fp", "sent", "new"].forEach(function (n) {
      var el = document.getElementById("jpa-" + n);
      if (el) { el.classList.toggle("jpa-hide", n !== name); }
    });
    var focusable = o.querySelector("#jpa-" + name + " .jpa-inp");
    if (focusable) { try { focusable.focus(); } catch (e) {} }
  }

  function setMsg(id, text, ok) {
    var m = document.getElementById(id);
    if (!m) { return; }
    m.textContent = text || "";
    m.classList.toggle("ok", !!ok);
  }

  function busy(btn, on, label) {
    if (!btn) { return; }
    btn.disabled = on;
    if (on) { btn._t = btn.innerHTML; btn.textContent = label || "Please wait\u2026"; }
    else if (btn._t) { btn.innerHTML = btn._t; }
  }

  function redirectTo() { return window.location.origin + window.location.pathname; }

  function wire(root) {
    // navigation links
    root.querySelectorAll("[data-go]").forEach(function (a) {
      a.addEventListener("click", function (e) { e.preventDefault(); showScreen(a.getAttribute("data-go")); });
    });
    // password reveals
    root.querySelectorAll("[data-eye]").forEach(function (eye) {
      eye.addEventListener("click", function () {
        var inp = eye.parentElement.querySelector(".jpa-pw"), i = eye.querySelector("i");
        if (inp.type === "password") { inp.type = "text"; i.className = "ti ti-eye-off"; }
        else { inp.type = "password"; i.className = "ti ti-eye"; }
      });
    });
    // Google (all sso buttons)
    root.querySelectorAll("[data-google]").forEach(function (b) {
      b.addEventListener("click", async function () {
        busy(b, true, "Redirecting\u2026");
        try { await client.auth.signInWithOAuth({ provider: "google", options: { redirectTo: redirectTo() } }); }
        catch (e) { busy(b, false); }
      });
    });

    // sign in
    document.getElementById("jpa-in").addEventListener("submit", async function (e) {
      e.preventDefault();
      var btn = this.querySelector(".jpa-cta");
      var email = document.getElementById("jpa-in-email").value.trim();
      var pass = document.getElementById("jpa-in-pass").value;
      setMsg("jpa-in-msg", "");
      if (!email || !pass) { setMsg("jpa-in-msg", "Enter your email and password."); return; }
      busy(btn, true, "Signing in\u2026");
      var out = await client.auth.signInWithPassword({ email: email, password: pass });
      if (out.error) { setMsg("jpa-in-msg", out.error.message || "Couldn\u2019t sign you in."); busy(btn, false); return; }
      setMsg("jpa-in-msg", "Signed in \u2014 loading\u2026", true);
      window.location.reload();
    });

    // sign up
    document.getElementById("jpa-up").addEventListener("submit", async function (e) {
      e.preventDefault();
      var btn = this.querySelector(".jpa-cta");
      var name = document.getElementById("jpa-up-name").value.trim();
      var email = document.getElementById("jpa-up-email").value.trim();
      var pass = document.getElementById("jpa-up-pass").value;
      setMsg("jpa-up-msg", "");
      if (!email || pass.length < 8) { setMsg("jpa-up-msg", "Use a valid email and a password of at least 8 characters."); return; }
      busy(btn, true, "Creating account\u2026");
      var out = await client.auth.signUp({ email: email, password: pass, options: { data: { name: name }, emailRedirectTo: redirectTo() } });
      if (out.error) { setMsg("jpa-up-msg", out.error.message || "Couldn\u2019t create your account."); busy(btn, false); return; }
      if (out.data && out.data.session) { window.location.href = "/app/profile"; return; }
      _lastEmail = email; _lastMode = "verify";
      showSent();
    });

    // forgot
    document.getElementById("jpa-fp").addEventListener("submit", async function (e) {
      e.preventDefault();
      var btn = this.querySelector(".jpa-cta");
      var email = document.getElementById("jpa-fp-email").value.trim();
      setMsg("jpa-fp-msg", "");
      if (!email) { setMsg("jpa-fp-msg", "Enter your email."); return; }
      busy(btn, true, "Sending\u2026");
      var out = await client.auth.resetPasswordForEmail(email, { redirectTo: redirectTo() });
      busy(btn, false);
      if (out.error) { setMsg("jpa-fp-msg", out.error.message || "Couldn\u2019t send the link."); return; }
      _lastEmail = email; _lastMode = "reset";
      showSent();
    });

    // set new password (after recovery link)
    document.getElementById("jpa-new").addEventListener("submit", async function (e) {
      e.preventDefault();
      var btn = this.querySelector(".jpa-cta");
      var pass = document.getElementById("jpa-new-pass").value;
      setMsg("jpa-new-msg", "");
      if (pass.length < 8) { setMsg("jpa-new-msg", "Use at least 8 characters."); return; }
      busy(btn, true, "Updating\u2026");
      var out = await client.auth.updateUser({ password: pass });
      if (out.error) { setMsg("jpa-new-msg", out.error.message || "Couldn\u2019t update your password."); busy(btn, false); return; }
      setMsg("jpa-new-msg", "Password updated \u2014 loading\u2026", true);
      window.location.reload();
    });

    // resend on the sent screen
    var resend = root.querySelector("[data-resend]");
    if (resend) {
      resend.addEventListener("click", async function () {
        if (!_lastEmail) { showScreen("in"); return; }
        busy(resend, true, "Resending\u2026");
        if (_lastMode === "reset") { await client.auth.resetPasswordForEmail(_lastEmail, { redirectTo: redirectTo() }); }
        else { await client.auth.resend({ type: "signup", email: _lastEmail, options: { emailRedirectTo: redirectTo() } }); }
        busy(resend, false);
        resend.textContent = "Sent again";
      });
    }
  }

  function showSent() {
    var h = document.getElementById("jpa-sent-h");
    var lead = document.getElementById("jpa-sent-lead");
    if (_lastMode === "verify") {
      if (h) { h.textContent = "Confirm your email"; }
      if (lead) { lead.innerHTML = "We sent a confirmation link to <b>" + esc(_lastEmail) + "</b>. Click it to finish setting up your account."; }
    } else {
      if (h) { h.textContent = "Check your email"; }
      if (lead) { lead.innerHTML = "We sent a reset link to <b>" + esc(_lastEmail) + "</b>. It expires in 30 minutes."; }
    }
    showScreen("sent");
  }

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]; }); }

  function hideAuth() { var el = document.getElementById(OVERLAY_ID); if (el) { el.remove(); } }

  function showFatal(text) {
    showAuth();
    setMsg("jpa-in-msg", text);
    var btn = document.querySelector("#jpa-in .jpa-cta");
    if (btn) { btn.disabled = true; }
  }

  // ── sign-out control (unchanged behaviour) ────────────────────────────────────
  function mountSignOut() { whenBody(_mountSignOut); }
  function _mountSignOut() {
    if (document.getElementById("jp-signout")) { return; }
    var b = document.createElement("button");
    b.id = "jp-signout";
    b.textContent = "Sign out";
    b.title = "Sign out of JobPilot";
    b.setAttribute("style", [
      "position:fixed", "bottom:12px", "left:12px", "z-index:9998",
      "padding:.35rem .7rem", "font-size:.75rem",
      "background:rgba(255,255,255,.92)", "color:#8A8985",
      "border:1px solid #EAE8E3", "border-radius:9px", "cursor:pointer",
      "font-family:'Inter',system-ui,sans-serif", "box-shadow:0 1px 3px rgba(24,24,22,.06)"
    ].join(";"));
    b.addEventListener("click", async function () {
      if (client) { await client.auth.signOut(); }
      window.location.reload();
    });
    document.body.appendChild(b);
  }

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

  ready = init();
})();
