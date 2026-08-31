/**
 * JobPilot Autofill — service worker.
 *
 * The content script runs in the page's origin and can't reliably reach
 * localhost, so all network access happens here, where the extension's host
 * permissions apply.
 *
 * It also owns the tab -> job binding. That binding is the safety-critical part
 * of this extension: attaching a resume means putting a document in front of a
 * real employer, so a wrong binding is worse than no binding. The rules:
 *
 *   - A binding is only made automatically on a confident URL match.
 *   - Anything less confident is offered as a suggestion in the popup, for you
 *     to confirm.
 *   - The binding is per tab, so two applications open in two tabs never cross.
 *   - Files are always requested BY JOB ID, so the bytes that come back are the
 *     document written for that job. Nothing is matched by filename or guessed.
 *
 * It also injects the content script. There is no content_scripts block in the
 * manifest: the extension is not present on any page until you click its icon.
 * `ensureInjected` puts it there on demand, for that one tab, on your say-so.
 */

// Where JobPilot is reachable. Defaults to localhost, but can be changed in the popup
// (Settings > Server URL) — e.g. to 127.0.0.1 if localhost is blocked, or to your
// tunnel URL. Read fresh on every call so a change takes effect without reloading.
const DEFAULT_API = "http://localhost:8000";

async function apiBase() {
  const { serverUrl } = await chrome.storage.local.get("serverUrl");
  return (serverUrl || DEFAULT_API).replace(/\/+$/, "");   // trim trailing slash
}

// tabId -> { id, title, company, confidence }
// Tab -> bound job. Kept in chrome.storage.session, not just a Map, because a Manifest V3
// service worker is torn down when idle and loses all in-memory state — which is why a
// bound job "deselected" itself the moment you clicked away and came back. session storage
// is cleared when the browser closes (bindings shouldn't outlive a session) but survives
// the worker sleeping. The Map is a synchronous cache in front of it.
const bindings = new Map();

async function bindingGet(tabId) {
  if (bindings.has(tabId)) return bindings.get(tabId);
  try {
    const { [`bind_${tabId}`]: saved } = await chrome.storage.session.get(`bind_${tabId}`);
    if (saved) bindings.set(tabId, saved);
    return saved || null;
  } catch {
    return bindings.get(tabId) || null;
  }
}

async function bindingSet(tabId, job) {
  bindings.set(tabId, job);
  try { await chrome.storage.session.set({ [`bind_${tabId}`]: job }); } catch { /* cache still holds it */ }
}

// Drop a tab's binding when the tab closes, so storage doesn't accumulate stale entries.
chrome.tabs.onRemoved.addListener((tabId) => {
  bindings.delete(tabId);
  chrome.storage.session.remove(`bind_${tabId}`).catch(() => {});
});

// Auth is Supabase now. The user signs in from the popup (email + password); that
// stores an access token and a refresh token. Every call carries the access token as
// a Bearer header, and a 401 triggers one silent refresh-and-retry before it's treated
// as "signed out". The public Supabase settings come from the server's open
// /api/public-config, so the extension needs no build-time config.
let _sbConfig = null;

async function supabaseConfig() {
  if (_sbConfig) return _sbConfig;
  try {
    const r = await fetch(`${await apiBase()}/api/public-config`);
    _sbConfig = await r.json();
  } catch {
    _sbConfig = {};
  }
  return _sbConfig;
}

async function authHeaders(extra = {}) {
  // Per-user extension key (entered in the popup, shown in the web app profile).
  const { extKey } = await chrome.storage.local.get("extKey");
  return extKey ? { ...extra, "X-JobPilot-Key": extKey } : extra;
}

async function refreshAccessToken() {
  const { sbRefreshToken } = await chrome.storage.local.get("sbRefreshToken");
  if (!sbRefreshToken) return false;
  const cfg = await supabaseConfig();
  if (!cfg.supabase_url || !cfg.supabase_anon_key) return false;
  try {
    const r = await fetch(`${cfg.supabase_url}/auth/v1/token?grant_type=refresh_token`, {
      method: "POST",
      headers: { apikey: cfg.supabase_anon_key, "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: sbRefreshToken }),
    });
    if (!r.ok) {
      await chrome.storage.local.remove(["sbAccessToken", "sbRefreshToken"]);
      return false;
    }
    const d = await r.json();
    await chrome.storage.local.set({
      sbAccessToken: d.access_token, sbRefreshToken: d.refresh_token,
    });
    return true;
  } catch {
    return false;
  }
}

// Central fetch: attach the per-user extension key. A key doesn't expire, so there's
// no token-refresh dance — a 401 just means the key is missing or wrong.
async function apiFetch(path, opts = {}) {
  const url = `${await apiBase()}${path}`;
  return fetch(url, { ...opts, headers: await authHeaders(opts.headers || {}) });
}

async function getJSON(path) {
  const r = await apiFetch(path);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function postJSON(path, body) {
  const r = await apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

/** Fetch a saved document as base64 — extension messaging can't carry binary. */
async function fetchFile(jobId, kind) {
  const r = await apiFetch(`/api/jobs/${jobId}/materials/${kind}/file?format=pdf`);
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${r.status}`);
  }

  // The server builds the filename from the job's company, so it is correct
  // by construction: Safin_Mahesania_Resume_Shopify.pdf
  const disposition = r.headers.get("Content-Disposition") || "";
  const nameMatch = disposition.match(/filename="?([^"]+)"?/);
  const name = nameMatch ? nameMatch[1] : `${kind}.pdf`;

  const bytes = new Uint8Array(await r.arrayBuffer());
  let binary = "";
  const CHUNK = 0x8000;                        // don't blow the call stack
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return { name, base64: btoa(binary), type: "application/pdf" };
}

/** Work out which job a page belongs to, and remember it for this tab. */
async function bindTab(tabId, url) {
  const res = await getJSON(`/api/jobs/match?url=${encodeURIComponent(url)}`);
  if (res.match) {
    await bindingSet(tabId, { ...res.match, confidence: res.confidence });
    return { bound: res.match, confidence: res.confidence, candidates: [] };
  }
  bindings.delete(tabId);                      // stale binding must not survive
  return { bound: null, confidence: "none", candidates: res.candidates || [] };
}

/**
 * Put the content script into a tab, on demand.
 *
 * Nothing is injected until you ask — no page you visit is touched, watched or
 * read unless you click the extension on it. Injecting twice is harmless: the
 * script guards itself and re-running a fill skips fields that are already set.
 */
async function ensureInjected(tabId) {
  // Already there? Then don't inject again.
  const alive = await new Promise((resolve) => {
    chrome.tabs.sendMessage(tabId, { type: "ping" }, (res) => {
      void chrome.runtime.lastError;      // no listener = not injected yet
      resolve(!!res?.ok);
    });
  });
  if (alive) return true;

  try {
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      files: ["content.js"],
    });
    return true;
  } catch (e) {
    // activeTab wasn't granted (the click didn't reach this tab), or the page is
    // one Chrome won't let extensions touch (chrome://, the Web Store).
    return false;
  }
}

chrome.tabs.onRemoved.addListener((tabId) => bindings.delete(tabId));

chrome.runtime.onMessage.addListener((msg, sender, respond) => {
  (async () => {
    const tabId = msg.tabId ?? sender.tab?.id;

    try {
      switch (msg.type) {
        case "signIn": {
          // Sign in with a JobPilot (Supabase) account and stash the tokens. The
          // popup collects the email/password; the exchange happens here so the
          // Supabase config and token storage stay in one place.
          try {
            const cfg = await supabaseConfig();
            if (!cfg.supabase_url || !cfg.supabase_anon_key) {
              respond({ ok: false, error: "Server sign-in isn't configured." });
              return;
            }
            const r = await fetch(`${cfg.supabase_url}/auth/v1/token?grant_type=password`, {
              method: "POST",
              headers: { apikey: cfg.supabase_anon_key, "Content-Type": "application/json" },
              body: JSON.stringify({ email: msg.email, password: msg.password }),
            });
            const d = await r.json().catch(() => ({}));
            if (!r.ok || !d.access_token) {
              respond({ ok: false, error: d.error_description || d.msg || "Invalid email or password." });
              return;
            }
            await chrome.storage.local.set({
              sbAccessToken: d.access_token, sbRefreshToken: d.refresh_token,
            });
            respond({ ok: true });
          } catch {
            respond({ ok: false, error: "Sign-in failed — is the server reachable?" });
          }
          return;
        }

        case "syncSession": {
          // The web app (any login method, including Google) hands us its Supabase
          // session so the extension works without a separate sign-in. We just store
          // the tokens the same way signIn does.
          if (msg.access_token && msg.refresh_token) {
            await chrome.storage.local.set({
              sbAccessToken: msg.access_token, sbRefreshToken: msg.refresh_token,
            });
            respond({ ok: true });
          } else {
            respond({ ok: false });
          }
          return;
        }

        case "health":
          try {
            await getJSON("/api/counts");
            respond({ ok: true });
          } catch (e) {
            // A 401/403 means the server is up but you're not signed in — a completely
            // different fix from "server not running". Tell them apart so the popup can
            // show the right message.
            const m = String(e && e.message || "");
            if (m.includes("401") || m.includes("403")) {
              respond({ ok: false, reason: "auth" });
            } else {
              respond({ ok: false, reason: "down" });
            }
          }
          return;

        // The popup asks for this before it tries to fill or attach.
        case "inject":
          respond({ ok: await ensureInjected(tabId) });
          return;

        case "getAnswers":
          respond({ ok: true, data: await getJSON("/api/autofill/data") });
          return;

        case "resolve":
          try {
            respond({
              ok: true,
              data: await postJSON("/api/autofill/resolve", {
                fields: msg.fields,
                job_id: (await bindingGet(tabId))?.id ?? null,
              }),
            });
          } catch (e) {
            const m = String(e && e.message || "");
            respond({ ok: false, reason: (m.includes("401") || m.includes("403")) ? "auth" : "down" });
          }
          return;

        // A single question pasted into the popup. Runs through the same resolve
        // endpoint as autofill (one field), so the answer is grounded in the profile
        // and the job, and invents nothing.
        case "ask":
          try {
            const data = await postJSON("/api/autofill/resolve", {
              fields: [{ id: "q", label: msg.question, type: "textarea", options: [] }],
              job_id: (await bindingGet(tabId))?.id ?? null,
            });
            respond({ ok: true, answer: (data.answers && data.answers.q) || "" });
          } catch (e) {
            const m = String(e && e.message || "");
            respond({ ok: false, reason: (m.includes("401") || m.includes("403")) ? "auth" : "down" });
          }
          return;

        // Which job is this page? Called when the popup opens.
        case "bind":
          respond({ ok: true, data: await bindTab(tabId, msg.url) });
          return;

        // You picked a job by hand in the popup.
        case "bindManual":
          await bindingSet(tabId, { ...msg.job, confidence: "manual" });
          respond({ ok: true, data: { bound: msg.job, confidence: "manual" } });
          return;

        case "getBinding":
          respond({ ok: true, data: (await bindingGet(tabId)) || null });
          return;

        case "searchJobs":
          respond({
            ok: true,
            data: await getJSON(`/api/jobs/search?q=${encodeURIComponent(msg.q || "")}`),
          });
          return;

        // What is saved for the bound job — drives the popup's attach button.
        case "getMaterials": {
          const job = await bindingGet(tabId);
          if (!job) { respond({ ok: false, error: "no job bound to this tab" }); return; }
          const data = await getJSON(`/api/jobs/${job.id}/materials`);
          respond({ ok: true, data: { job, ...data } });
          return;
        }

        // The bytes to attach. Requested by job id — never by filename.
        case "getFiles": {
          const job = await bindingGet(tabId);
          if (!job) { respond({ ok: false, error: "no job bound to this tab" }); return; }

          const files = {};
          for (const kind of msg.kinds || ["resume", "cover"]) {
            try {
              files[kind] = await fetchFile(job.id, kind);
            } catch (e) {
              files[kind] = { error: String(e.message || e) };
            }
          }
          respond({ ok: true, data: { job, files } });
          return;
        }

        default:
          respond({ ok: false, error: "unknown message" });
      }
    } catch (e) {
      respond({ ok: false, error: String(e.message || e) });
    }
  })();

  return true;                 // keep the channel open for the async reply
});
