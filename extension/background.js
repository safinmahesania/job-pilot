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

const API = "http://localhost:8000";

// tabId -> { id, title, company, confidence }
const bindings = new Map();

async function getJSON(path) {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function postJSON(path, body) {
  const r = await fetch(`${API}${path}`, {
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
  const r = await fetch(`${API}/api/jobs/${jobId}/materials/${kind}/file?format=pdf`);
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
    bindings.set(tabId, { ...res.match, confidence: res.confidence });
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
        case "health":
          await getJSON("/api/autofill/data");
          respond({ ok: true });
          return;

        // The popup asks for this before it tries to fill or attach.
        case "inject":
          respond({ ok: await ensureInjected(tabId) });
          return;

        case "getAnswers":
          respond({ ok: true, data: await getJSON("/api/autofill/data") });
          return;

        case "resolve":
          respond({
            ok: true,
            data: await postJSON("/api/autofill/resolve", {
              fields: msg.fields,
              job_id: bindings.get(tabId)?.id ?? null,
            }),
          });
          return;

        // Which job is this page? Called when the popup opens.
        case "bind":
          respond({ ok: true, data: await bindTab(tabId, msg.url) });
          return;

        // You picked a job by hand in the popup.
        case "bindManual":
          bindings.set(tabId, { ...msg.job, confidence: "manual" });
          respond({ ok: true, data: { bound: msg.job, confidence: "manual" } });
          return;

        case "getBinding":
          respond({ ok: true, data: bindings.get(tabId) || null });
          return;

        case "searchJobs":
          respond({
            ok: true,
            data: await getJSON(`/api/jobs/search?q=${encodeURIComponent(msg.q || "")}`),
          });
          return;

        // What is saved for the bound job — drives the popup's attach button.
        case "getMaterials": {
          const job = bindings.get(tabId);
          if (!job) { respond({ ok: false, error: "no job bound to this tab" }); return; }
          const data = await getJSON(`/api/jobs/${job.id}/materials`);
          respond({ ok: true, data: { job, ...data } });
          return;
        }

        // The bytes to attach. Requested by job id — never by filename.
        case "getFiles": {
          const job = bindings.get(tabId);
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
