/**
 * JobPilot Autofill — popup.
 *
 * Two jobs: show you exactly which posting this tab is bound to (so a wrong
 * binding is caught by you, not discovered by an employer), and let you fill or
 * attach with one click.
 *
 * Nothing is ever attached without a bound job. If the URL doesn't confidently
 * match a job in your database, the attach button stays disabled until you pick
 * one by hand.
 */

const $ = (id) => document.getElementById(id);

const dot = $("dot"), statusText = $("statusText"), foot = $("foot");
const jobBox = $("jobBox"), jobTitle = $("jobTitle"),
      jobCompany = $("jobCompany"), jobWhy = $("jobWhy");
const changeJob = $("changeJob"), picker = $("picker"),
      search = $("search"), results = $("results");
const fillBtn = $("fillBtn"), attachBtn = $("attachBtn");
const askInput = $("askInput"), askBtn = $("askBtn"), askResult = $("askResult"),
      askAnswer = $("askAnswer"), askCopy = $("askCopy");
const toggles = { autoFill: $("autoFill"), useAI: $("useAI") };

const DEFAULTS = { enabled: true, autoFill: false, useAI: true };
let settings = { ...DEFAULTS };
let tabId = null;
let bound = null;        // { id, title, company, confidence }
let materials = [];      // what's saved for the bound job

const WHY = {
  exact: "Matched by URL.",
  path: "Matched by URL path — check this is right.",
  manual: "You picked this job.",
};

const send = (msg) =>
  new Promise((resolve) => {
    chrome.runtime.sendMessage({ ...msg, tabId }, (res) => {
      if (chrome.runtime.lastError) resolve({ ok: false, error: "disconnected" });
      else resolve(res);
    });
  });

// ── Rendering ───────────────────────────────────────────────────────────────

function paintToggles() {
  for (const [key, el] of Object.entries(toggles)) {
    el.classList.toggle("on", !!settings[key]);
  }
}

function paintJob() {
  jobBox.classList.remove("bound", "none");

  if (!bound) {
    jobBox.classList.add("none");
    jobTitle.textContent = "No job matched";
    jobCompany.textContent = "";
    jobWhy.textContent =
      "This page doesn't match a job in JobPilot. Pick one so the right documents get attached.";
    changeJob.textContent = "Pick a job";
    attachBtn.disabled = true;
    attachBtn.textContent = "Attach resume & cover letter";
    return;
  }

  jobBox.classList.add("bound");
  jobTitle.textContent = bound.title || "(untitled)";
  jobCompany.textContent = bound.company || "";
  jobWhy.textContent = WHY[bound.confidence] || "";
  changeJob.textContent = "Change job";

  // The attach button is only useful if something is actually saved.
  const kinds = materials.map((m) => m.kind);
  if (!kinds.length) {
    attachBtn.disabled = true;
    attachBtn.textContent = "No documents saved for this job";
    foot.textContent =
      "Generate a resume or cover letter for this job in JobPilot, then come back.";
  } else {
    attachBtn.disabled = false;
    const label = kinds
      .map((k) => (k === "cover" ? "cover letter" : "resume"))
      .join(" & ");
    attachBtn.textContent = `Attach ${label}`;
    foot.textContent = "Existing answers and uploads are never overwritten.";
  }
}

async function refreshMaterials() {
  materials = [];
  if (!bound) return;
  const res = await send({ type: "getMaterials" });
  if (res?.ok) materials = res.data.materials || [];
}

// ── Job binding ─────────────────────────────────────────────────────────────

async function bindCurrentTab(url) {
  const res = await send({ type: "bind", url });
  if (!res?.ok) return;

  bound = res.data.bound;
  await refreshMaterials();
  paintJob();

  // Ambiguous match: show the candidates rather than choosing for you.
  if (!bound && res.data.candidates?.length) {
    showPicker(res.data.candidates);
  }
}

function showPicker(list) {
  picker.style.display = "block";
  renderResults(list);
  search.focus();
}

function renderResults(list) {
  results.innerHTML = "";
  if (!list.length) {
    results.innerHTML =
      '<div class="result"><div class="c">No jobs found</div></div>';
    return;
  }
  for (const job of list) {
    const row = document.createElement("div");
    row.className = "result";
    row.innerHTML =
      `<div class="t"></div><div class="c"></div>`;
    row.querySelector(".t").textContent = job.title || "(untitled)";
    row.querySelector(".c").textContent = job.company || "";
    row.addEventListener("click", async () => {
      const res = await send({ type: "bindManual", job });
      if (res?.ok) {
        bound = { ...job, confidence: "manual" };
        await refreshMaterials();
        picker.style.display = "none";
        paintJob();
      }
    });
    results.appendChild(row);
  }
}

changeJob.addEventListener("click", async () => {
  const open = picker.style.display === "block";
  picker.style.display = open ? "none" : "block";
  if (!open) {
    const res = await send({ type: "searchJobs", q: "" });
    if (res?.ok) renderResults(res.data);
    search.focus();
  }
});

let searchTimer = null;
search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    const res = await send({ type: "searchJobs", q: search.value });
    if (res?.ok) renderResults(res.data);
  }, 200);
});

// ── Actions ─────────────────────────────────────────────────────────────────

function withButton(btn, working, done) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = working;
  return (message) => {
    btn.textContent = message || done;
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = original;
    }, 1800);
  };
}

/** The content script is only injected when you act — never on page load. */
async function inject() {
  const res = await send({ type: "inject" });
  return !!res?.ok;
}

fillBtn.addEventListener("click", async () => {
  const finish = withButton(fillBtn, "Filling…", "Done");
  if (!(await inject())) {
    foot.textContent = "Chrome won't let extensions run on this page.";
    finish("Can't run here");
    return;
  }
  chrome.tabs.sendMessage(tabId, { type: "fillNow", settings }, (res) => {
    if (chrome.runtime.lastError || !res) {
      foot.textContent = "Couldn't reach this page. Reload it and try again.";
      finish("Failed");
      return;
    }
    finish(res.filled
      ? `Filled ${res.filled} field${res.filled === 1 ? "" : "s"}`
      : "Nothing matched");
    if (res.skipped) {
      foot.textContent = `${res.skipped} field${res.skipped === 1 ? "" : "s"} left for you — review before submitting.`;
    }
  });
});

attachBtn.addEventListener("click", async () => {
  if (!bound) return;                       // guarded, but never hurts
  const finish = withButton(attachBtn, "Attaching…", "Attached");
  if (!(await inject())) {
    foot.textContent = "Chrome won't let extensions run on this page.";
    finish("Can't run here");
    return;
  }
  chrome.tabs.sendMessage(tabId, { type: "attachNow", settings }, (res) => {
    if (chrome.runtime.lastError || !res) {
      foot.textContent = "Couldn't reach this page. Reload it and try again.";
      finish("Failed");
      return;
    }
    finish(res.attached
      ? `Attached ${res.attached} file${res.attached === 1 ? "" : "s"}`
      : "Nothing attached");
  });
});

for (const [key, el] of Object.entries(toggles)) {
  el.addEventListener("click", () => {
    settings[key] = !settings[key];
    paintToggles();
    chrome.storage.local.set(settings);
    chrome.tabs.sendMessage(tabId, { type: "settings", settings }, () => {
      void chrome.runtime.lastError;
    });
  });
}

// Ask a question — paste any application question, get an answer from your profile
// (grounded, first-person) to copy into the form. Uses the same resolve endpoint as
// autofill, so it obeys the same "never invent a fact" rules.
if (askBtn) {
  askBtn.addEventListener("click", async () => {
    const q = (askInput.value || "").trim();
    if (!q) { askInput.focus(); return; }
    const finish = withButton(askBtn, "Thinking…", "Generate answer");
    askResult.style.display = "none";
    const res = await send({ type: "ask", question: q });
    finish("Generate answer");
    if (res?.ok && res.answer) {
      askAnswer.value = res.answer;
      askResult.style.display = "block";
    } else if (res?.reason === "auth") {
      foot.textContent = "JobPilot needs its password — enter it below.";
    } else if (res?.answer === "") {
      askAnswer.value = "(Your profile doesn't answer this one — add the detail to your profile and try again.)";
      askResult.style.display = "block";
    } else {
      foot.textContent = "Couldn't generate an answer. Is JobPilot running?";
    }
  });
}

if (askCopy) {
  askCopy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(askAnswer.value);
      const orig = askCopy.textContent;
      askCopy.textContent = "Copied ✓";
      setTimeout(() => { askCopy.textContent = orig; }, 1500);
    } catch {
      askAnswer.select();          // fallback: select so the user can Ctrl+C
      document.execCommand("copy");
    }
  });
}

// ── Boot ────────────────────────────────────────────────────────────────────

(async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  tabId = tab?.id ?? null;

  chrome.storage.local.get(Object.keys(DEFAULTS), (stored) => {
    settings = { ...DEFAULTS, ...stored };
    paintToggles();
  });

  // The server URL. Saved as you type; background.js reads it on every call. Changing
  // it re-checks the connection so you see immediately whether the new address works.
  const urlInput = document.getElementById("serverUrl");
  if (urlInput) {
    chrome.storage.local.get("serverUrl", ({ serverUrl }) => {
      urlInput.value = serverUrl || "http://localhost:8000";
    });
    let urlTimer = null;
    urlInput.addEventListener("input", () => {
      chrome.storage.local.set({ serverUrl: urlInput.value.trim() });
      clearTimeout(urlTimer);
      urlTimer = setTimeout(() => location.reload(), 600);   // re-run health check
    });
  }

  // The app password, if the app is locked. Saved as you type; sent by
  // background.js as the x-jobpilot-key header on every call.
  const keyInput = document.getElementById("apiKey");
  if (keyInput) {
    chrome.storage.local.get("apiKey", ({ apiKey }) => {
      if (apiKey) keyInput.value = apiKey;
    });
    let keyTimer = null;
    keyInput.addEventListener("input", () => {
      chrome.storage.local.set({ apiKey: keyInput.value.trim() });
      clearTimeout(keyTimer);
      keyTimer = setTimeout(() => location.reload(), 600);   // re-check with new password
    });
  }

  const health = await send({ type: "health" });
  if (!health?.ok) {
    dot.classList.add("bad");
    if (health?.reason === "auth") {
      statusText.textContent = "Password needed";
      foot.textContent = "Your JobPilot is locked — enter its password below.";
      jobTitle.textContent = "—";
      jobWhy.textContent = "Enter the password (JOBPILOT_PASSWORD) below to connect.";
    } else {
      const url = urlInput?.value || "http://localhost:8000";
      statusText.textContent = "Can't reach JobPilot";
      foot.textContent = `Tried ${url}. Is it running? Check the Server URL below.`;
      jobTitle.textContent = "—";
      jobWhy.textContent = "Start JobPilot, or fix the Server URL, then reopen this.";
    }
    return;
  }

  dot.classList.add("ok");
  statusText.textContent = "Connected to JobPilot";
  fillBtn.disabled = false;

  if (tab?.url) await bindCurrentTab(tab.url);
})();
