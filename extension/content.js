/**
 * JobPilot Autofill — content script.
 *
 * Runs on every page. Does nothing until it finds something that looks like a
 * job application form, and (unless auto-fill is on) nothing until you ask.
 *
 * How it fills a form:
 *   1. Scan every visible input / select / textarea and work out its label.
 *   2. Match each field against the canonical answers from the JobPilot API
 *      using local heuristics — instant, no AI, no network beyond one fetch.
 *   3. Anything the heuristics can't place is sent to the API in ONE batch, where
 *      the model maps it to the profile (or returns blank rather than guessing).
 *   4. Set the value the way React/Vue actually notice — via the native setter,
 *      then dispatch input + change events.
 *
 * Multi-step forms (Workday, Oracle, Phenom):
 *   These swap the DOM in place instead of loading a new page, so a one-shot fill
 *   would only ever fill step 1. A debounced MutationObserver plus a patched
 *   History API means each new step is detected and filled the same way. Fields
 *   already filled are never overwritten, so re-running is safe.
 */

// Injected on demand by the service worker when you click the extension — never
// on page load, and never on a page you didn't point it at. Guard against being
// injected twice into the same document.
if (window.__jobpilotLoaded) {
  // already here; do nothing
} else {
  window.__jobpilotLoaded = true;
}

// Fields we never touch, whatever their label says.
const SKIP_TYPES = new Set([
  "password", "file", "hidden", "submit", "button", "image", "reset", "search",
]);

/**
 * Heuristic field map. First pattern to match a field's label/name/id/placeholder
 * wins. Keys are the canonical answer keys returned by /api/autofill/data.
 *
 * Order matters: more specific patterns must come before general ones
 * ("first name" before "name", "postal code" before "code").
 */
const RULES = [
  ["first_name",          /\b(first|given)[\s_-]*name\b|^fname$/i],
  ["last_name",           /\b(last|family|sur)[\s_-]*name\b|^lname$/i],
  ["full_name",           /\b(full|legal|your)?[\s_-]*name\b/i],
  ["email",               /\be-?mail\b/i],
  ["phone",               /\b(phone|mobile|telephone|cell)\b/i],
  ["linkedin",            /\blinked-?in\b/i],
  ["github",              /\bgit-?hub\b/i],
  ["website",             /\b(website|portfolio|personal site|url)\b/i],
  ["postal_code",         /\b(postal|zip)[\s_-]*code\b|\bzip\b/i],
  ["address",             /\b(street|address(?!\s*line\s*2)|address line 1)\b/i],
  ["city",                /\b(city|town|locality)\b/i],
  ["province",            /\b(province|state|region)\b/i],
  ["country",             /\bcountry\b/i],
  ["current_company",     /\b(current|present)?[\s_-]*(employer|company)\b/i],
  ["current_title",       /\b(current)?[\s_-]*(job title|position|role)\b/i],
  ["school",              /\b(school|university|college|institution)\b/i],
  ["degree",              /\bdegree\b/i],
  ["field_of_study",      /\b(field of study|major|discipline)\b/i],
  ["graduation_year",     /\b(graduation|grad)[\s_-]*(year|date)\b/i],
  ["years_of_experience", /\byears?[\s_-]*(of)?[\s_-]*experience\b/i],
  ["salary_expectation",  /\b(salary|compensation)[\s_-]*(expectation|requirement)?\b/i],
  ["notice_period",       /\b(notice period|start date|availability|when can you start)\b/i],
  ["how_did_you_hear",    /\bhow did you (hear|find)\b/i],
  ["work_authorized",     /\b(legally |authorized|authorised|eligible)[\s\S]{0,40}\bwork\b/i],
  ["needs_sponsorship",   /\bsponsor(ship)?\b/i],
  ["requires_visa",       /\bvisa\b/i],
  ["willing_to_relocate", /\brelocat(e|ion)\b/i],
  ["gender",              /\bgender\b/i],
  ["ethnicity",           /\b(ethnicity|race|hispanic)\b/i],
  ["veteran_status",      /\bveteran\b/i],
  ["disability_status",   /\bdisabilit(y|ies)\b/i],
];

// Yes/no questions where the profile stores a boolean.
const BOOLEAN_KEYS = new Set([
  "work_authorized", "needs_sponsorship", "requires_visa", "willing_to_relocate",
]);

// Demographic questions are never auto-answered unless the profile has a value.
const VOLUNTARY_KEYS = new Set([
  "gender", "ethnicity", "veteran_status", "disability_status",
]);

let answers = null;      // canonical answers from the API
let custom = [];         // the user's own keyword -> answer rules
let settings = { enabled: true, autoFill: false, useAI: true, jobId: null };
let filling = false;     // guards against re-entrant fills
const aiCache = new Map();   // label -> answer, so a field is only resolved once

// ── Utilities ───────────────────────────────────────────────────────────────

const isVisible = (el) => {
  if (!el || el.disabled || el.readOnly) return false;
  const style = window.getComputedStyle(el);
  if (style.display === "none" || style.visibility === "hidden") return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
};

/** A field already has a value the user (or we) put there. */
const hasValue = (el) => {
  if (el.type === "checkbox" || el.type === "radio") return el.checked;
  return !!(el.value && el.value.trim());
};

/**
 * Find the human-readable label for a field. ATS markup varies wildly, so try
 * every reasonable source and fall back to the surrounding text.
 */
function labelFor(el) {
  const bits = [];

  if (el.id) {
    const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (l) bits.push(l.innerText);
  }
  const wrapping = el.closest("label");
  if (wrapping) bits.push(wrapping.innerText);

  if (el.getAttribute("aria-label")) bits.push(el.getAttribute("aria-label"));

  const labelledBy = el.getAttribute("aria-labelledby");
  if (labelledBy) {
    labelledBy.split(/\s+/).forEach((id) => {
      const node = document.getElementById(id);
      if (node) bits.push(node.innerText);
    });
  }

  // Workday and friends: the question sits in a wrapper above the input.
  const group = el.closest("[data-automation-id], .field, .form-group, fieldset");
  if (group) {
    const legend = group.querySelector("legend, label, .field-label");
    if (legend) bits.push(legend.innerText);
  }

  bits.push(el.name || "", el.id || "", el.placeholder || "");

  return bits.join(" ").replace(/\s+/g, " ").trim().slice(0, 300);
}

/** Match a field's text against the rules; returns a canonical key or null. */
function matchKey(text) {
  for (const [key, pattern] of RULES) {
    if (pattern.test(text)) return key;
  }
  return null;
}

/**
 * Your own answers from profile.yaml (`custom_answers`). A rule matches when
 * EVERY one of its keywords appears in the field's label. Checked before the AI,
 * so recurring questions are answered exactly, instantly and for free.
 */
function matchCustom(text) {
  const haystack = text.toLowerCase();
  for (const rule of custom) {
    if (rule.match.every((word) => haystack.includes(word))) return rule.answer;
  }
  return null;
}

/**
 * Set a value the way a framework-controlled input will actually register.
 * Assigning .value directly is silently ignored by React, which tracks its own
 * value on the node — so go through the native setter, then fire the events.
 */
function setValue(el, value) {
  const proto = el instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : el instanceof HTMLSelectElement
      ? HTMLSelectElement.prototype
      : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (setter) setter.call(el, value);
  else el.value = value;

  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));
}

/** Pick the option in a <select> that best matches the answer. */
function fillSelect(el, answer) {
  const want = String(answer).trim().toLowerCase();
  if (!want) return false;

  const options = Array.from(el.options).filter((o) => o.value !== "");
  let hit =
    options.find((o) => o.text.trim().toLowerCase() === want) ||
    options.find((o) => o.value.trim().toLowerCase() === want) ||
    options.find((o) => o.text.trim().toLowerCase().includes(want)) ||
    options.find((o) => want.includes(o.text.trim().toLowerCase()) && o.text.trim());

  if (!hit) return false;
  setValue(el, hit.value);
  return true;
}

/** Tick the radio in a group whose label matches the answer. */
function fillRadio(el, answer) {
  const want = String(answer).trim().toLowerCase();
  if (!want || !el.name) return false;

  const group = document.querySelectorAll(
    `input[type="radio"][name="${CSS.escape(el.name)}"]`
  );
  for (const radio of group) {
    const text = labelFor(radio).toLowerCase();
    if (text.includes(want) || want.includes(text.trim())) {
      radio.click();                      // click() so frameworks see it
      return true;
    }
  }
  return false;
}

/** Booleans become the wording the form expects. */
function asAnswer(key, raw) {
  if (BOOLEAN_KEYS.has(key)) {
    if (raw === true || raw === "true" || raw === "True") return "Yes";
    if (raw === false || raw === "false" || raw === "False") return "No";
  }
  return raw === null || raw === undefined ? "" : String(raw);
}

// ── The fill itself ─────────────────────────────────────────────────────────

/** Every field on the page we could plausibly fill. */
function collectFields() {
  const nodes = document.querySelectorAll("input, select, textarea");
  return Array.from(nodes).filter((el) => {
    if (SKIP_TYPES.has(el.type)) return false;
    if (!isVisible(el)) return false;
    if (hasValue(el)) return false;             // never overwrite existing input
    return true;
  });
}

async function fillPage({ silent = false } = {}) {
  if (filling) return { filled: 0, skipped: 0 };
  filling = true;

  try {
    if (!answers) {
      const res = await send({ type: "getAnswers" });
      if (!res?.ok) {
        if (!silent) toast("Can't reach JobPilot — is it running?", "error");
        return { filled: 0, skipped: 0 };
      }
      answers = res.data.answers;
      custom = res.data.custom || [];
    }

    const fields = collectFields();
    if (!fields.length) {
      if (!silent) toast("Nothing to fill on this page", "info");
      return { filled: 0, skipped: 0 };
    }

    let filled = 0;
    const unresolved = [];

    // Pass 1 — your own custom rules first, then the built-in heuristics.
    for (const el of fields) {
      const text = labelFor(el);

      // Your explicit answers win over everything else.
      const mine = matchCustom(text);
      if (mine && applyValue(el, mine)) { filled++; continue; }

      const key = matchKey(text);
      if (key) {
        const value = asAnswer(key, answers[key]);
        // Blank profile value: leave the field alone. Voluntary questions are
        // never guessed at.
        if (!value) {
          if (!VOLUNTARY_KEYS.has(key)) unresolved.push({ el, text });
          continue;
        }
        if (applyValue(el, value)) { filled++; continue; }
      }

      if (!VOLUNTARY_KEYS.has(key)) unresolved.push({ el, text });
    }

    // Pass 2 — one batched AI call for whatever is left.
    if (settings.useAI && unresolved.length) {
      const pending = [];
      for (const item of unresolved) {
        const cached = aiCache.get(item.text);
        if (cached !== undefined) {
          if (cached && applyValue(item.el, cached)) filled++;
        } else {
          pending.push(item);
        }
      }

      if (pending.length) {
        const payload = pending.map((item, i) => ({
          id: `f${i}`,
          label: item.text,
          type: item.el.tagName === "SELECT" ? "select"
            : item.el.tagName === "TEXTAREA" ? "textarea"
              : item.el.type || "text",
          options: item.el.tagName === "SELECT"
            ? Array.from(item.el.options).map((o) => o.text.trim()).filter(Boolean)
            : [],
        }));

        // This is the slow part: the AI writes answers for fields no rule matched
        // (essays, per-tech experience). On a local model it can take 20-40s, so show
        // a persistent loader — otherwise it looks frozen and people give up or refill.
        showLoader(`JobPilot is writing ${pending.length} answer${pending.length === 1 ? "" : "s"}…`);
        let res;
        try {
          res = await send({
            type: "resolve",
            fields: payload,
            jobId: settings.jobId,
          });
        } finally {
          hideLoader();
        }

        if (res?.ok) {
          const mapped = res.data.answers || {};
          pending.forEach((item, i) => {
            const answer = mapped[`f${i}`] || "";
            aiCache.set(item.text, answer);       // remember, even if blank
            if (answer && applyValue(item.el, answer)) filled++;
          });
        } else if (res && res.reason === "auth") {
          if (!silent) toast("JobPilot needs its password — set it in the extension popup", "error");
        }
      }
    }

    const skipped = collectFields().length;      // still empty after the pass
    if (!silent) {
      toast(
        filled
          ? `Filled ${filled} field${filled === 1 ? "" : "s"}` +
            (skipped ? ` · ${skipped} left for you` : "")
          : "Nothing matched your profile",
        filled ? "success" : "info"
      );
    }
    return { filled, skipped };
  } finally {
    filling = false;
  }
}

/** Route a value to the right filler for the element type. */
function applyValue(el, value) {
  try {
    if (el.tagName === "SELECT") return fillSelect(el, value);
    if (el.type === "radio") return fillRadio(el, value);
    if (el.type === "checkbox") {
      const yes = /^(yes|true|1)$/i.test(String(value));
      if (yes !== el.checked) el.click();
      return yes;
    }
    setValue(el, value);
    return true;
  } catch {
    return false;
  }
}


// ── File attachment ─────────────────────────────────────────────────────────
//
// The resume and cover letter are fetched from JobPilot BY JOB ID (the service
// worker holds the tab -> job binding), turned back into real File objects, and
// dropped into the page's file inputs via DataTransfer — which is the only way
// to populate an <input type="file"> programmatically that the page will accept.
//
// Which input gets which document is decided from the input's own label. If an
// input can't be identified, it is left alone: a cover letter uploaded into the
// resume slot is worse than an empty slot you fill yourself.

const RESUME_PATTERN = /\b(resume|résumé|cv|curriculum)\b/i;
const COVER_PATTERN = /\b(cover[\s_-]*letter|covering[\s_-]*letter|motivation)\b/i;

/** Turn the base64 the service worker sent back into a File. */
function toFile({ name, base64, type }) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new File([bytes], name, { type });
}

/** Put a File into an <input type="file"> the way the page will notice. */
function attachTo(input, file) {
  const dt = new DataTransfer();
  dt.items.add(file);
  input.files = dt.files;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
}

/** Every file input on the page, tagged with what it seems to want. */
function fileInputs() {
  return Array.from(document.querySelectorAll('input[type="file"]'))
    .filter((el) => !el.disabled && !el.files?.length)   // don't replace an upload
    .map((el) => {
      const text = labelFor(el) + " " + (el.getAttribute("accept") || "");
      let kind = null;
      if (COVER_PATTERN.test(text)) kind = "cover";        // check cover first:
      else if (RESUME_PATTERN.test(text)) kind = "resume"; // "resume or cover letter"
      return { el, kind, text };
    });
}

/**
 * Attach the saved documents for the job bound to this tab.
 * Returns {attached, skipped, reason}.
 */
async function attachFiles() {
  const inputs = fileInputs();
  if (!inputs.length) {
    toast("No file uploads on this page", "info");
    return { attached: 0 };
  }

  const identified = inputs.filter((i) => i.kind);
  if (!identified.length) {
    toast(`${inputs.length} upload field(s) here, but I can't tell what they want — attach by hand`, "info");
    return { attached: 0 };
  }

  const kinds = [...new Set(identified.map((i) => i.kind))];
  const res = await send({ type: "getFiles", kinds });

  if (!res?.ok) {
    // The commonest cause: no job is bound to this tab.
    toast(res?.error === "no job bound to this tab"
      ? "Open the extension and pick which job this is first"
      : `Couldn't fetch your documents: ${res?.error || "unknown error"}`, "error");
    return { attached: 0 };
  }

  const { job, files } = res.data;
  let attached = 0;
  const missing = [];

  for (const { el, kind } of identified) {
    const payload = files[kind];
    if (!payload || payload.error) {
      missing.push(kind);
      continue;
    }
    if (attachTo(el, toFile(payload))) attached++;
  }

  if (attached) {
    toast(`Attached ${attached} file${attached === 1 ? "" : "s"} for ${job.company} — ${job.title}`, "success");
  }
  if (missing.length) {
    toast(`No saved ${missing.join(" or ")} for this job — generate it in JobPilot first`, "info");
  }
  return { attached, job };
}

// ── Multi-step support ──────────────────────────────────────────────────────
//
// Workday/Oracle/Phenom replace the form in place rather than navigating, so we
// watch for the DOM settling down and re-run. Filled fields are skipped, so
// re-running is cheap and idempotent.

let debounce = null;

function scheduleAutoFill() {
  if (!settings.enabled || !settings.autoFill) return;
  clearTimeout(debounce);
  debounce = setTimeout(async () => {
    if (looksLikeApplicationForm()) {
      const { filled } = await fillPage({ silent: true });
      if (filled) toast(`Filled ${filled} field${filled === 1 ? "" : "s"}`, "success");
    }
  }, 800);   // let the step finish rendering before we touch it
}

/** Cheap check so we don't poke around on ordinary pages. */
function looksLikeApplicationForm() {
  const inputs = document.querySelectorAll(
    "input[type='text'], input[type='email'], input[type='tel'], textarea, select"
  );
  if (inputs.length < 3) return false;
  const text = document.body.innerText.slice(0, 4000).toLowerCase();
  return /apply|application|resume|cv|cover letter|first name|work authorization/.test(text);
}

function watchForSteps() {
  const observer = new MutationObserver(() => scheduleAutoFill());
  observer.observe(document.body, { childList: true, subtree: true });

  // SPA route changes don't fire a load event — patch the History API.
  for (const method of ["pushState", "replaceState"]) {
    const original = history[method];
    history[method] = function (...args) {
      const out = original.apply(this, args);
      window.dispatchEvent(new Event("jobpilot:navigated"));
      return out;
    };
  }
  window.addEventListener("jobpilot:navigated", scheduleAutoFill);
  window.addEventListener("popstate", scheduleAutoFill);
}

// ── Messaging + UI ──────────────────────────────────────────────────────────

/** All network calls go through the service worker (it holds the permissions). */
function send(message) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(message, (response) => {
      if (chrome.runtime.lastError) resolve({ ok: false });
      else resolve(response);
    });
  });
}

// A persistent loader for the slow AI phase. Unlike a toast (which auto-dismisses),
// this stays until hideLoader() is called, with a spinner so it's clearly "working",
// not "stuck". One is injected once; showLoader just updates its text and shows it.
function showLoader(message) {
  let el = document.getElementById("jobpilot-loader");
  if (!el) {
    // The spinner keyframes, injected once.
    if (!document.getElementById("jobpilot-loader-style")) {
      const style = document.createElement("style");
      style.id = "jobpilot-loader-style";
      style.textContent =
        "@keyframes jobpilot-spin{to{transform:rotate(360deg)}}";
      document.head.appendChild(style);
    }
    el = document.createElement("div");
    el.id = "jobpilot-loader";
    Object.assign(el.style, {
      position: "fixed", bottom: "20px", right: "20px", zIndex: 2147483647,
      background: "#16284f", color: "#fff", padding: "12px 16px",
      borderRadius: "10px", fontSize: "13px",
      fontFamily: "system-ui, sans-serif", boxShadow: "0 4px 18px rgba(0,0,0,.28)",
      display: "flex", alignItems: "center", gap: "10px", maxWidth: "320px",
    });
    const spinner = document.createElement("div");
    Object.assign(spinner.style, {
      width: "16px", height: "16px", borderRadius: "50%",
      border: "2px solid rgba(255,255,255,.35)", borderTopColor: "#fff",
      animation: "jobpilot-spin .7s linear infinite", flexShrink: "0",
    });
    const text = document.createElement("span");
    text.id = "jobpilot-loader-text";
    el.appendChild(spinner);
    el.appendChild(text);
    document.body.appendChild(el);
  }
  document.getElementById("jobpilot-loader-text").textContent = message || "JobPilot is thinking…";
  el.style.display = "flex";
}

function hideLoader() {
  document.getElementById("jobpilot-loader")?.remove();
}

function toast(message, kind = "info") {
  document.getElementById("jobpilot-toast")?.remove();
  const colors = {
    success: "#1D9E75", error: "#DC2626", info: "#B4791A",
  };
  const el = document.createElement("div");
  el.id = "jobpilot-toast";
  el.textContent = message;
  Object.assign(el.style, {
    position: "fixed", bottom: "20px", right: "20px", zIndex: 2147483647,
    background: colors[kind] || colors.info, color: "#fff",
    padding: "10px 16px", borderRadius: "8px", fontSize: "13px",
    fontFamily: "system-ui, sans-serif", boxShadow: "0 4px 14px rgba(0,0,0,.2)",
    maxWidth: "320px",
  });
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

// Popup asks us to fill, or reports a settings change.
chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
  if (msg.type === "fillNow") {
    answers = null;                    // always take a fresh profile
    aiCache.clear();
    settings = { ...settings, ...msg.settings };
    fillPage().then((r) => respond(r));
    return true;                       // async response
  }
  if (msg.type === "attachNow") {
    settings = { ...settings, ...msg.settings };
    attachFiles().then((r) => respond(r));
    return true;                       // async response
  }
  if (msg.type === "settings") {
    settings = { ...settings, ...msg.settings };
    respond({ ok: true });
  }
  if (msg.type === "ping") {
    respond({
      ok: true,
      form: looksLikeApplicationForm(),
      uploads: document.querySelectorAll('input[type="file"]').length,
    });
  }
  return true;
});

// ── Boot ────────────────────────────────────────────────────────────────────

chrome.storage.local.get(["enabled", "autoFill", "useAI"], (stored) => {
  settings = { ...settings, ...stored };

  // Multi-step forms swap the DOM in place rather than navigating, so once we are
  // here we keep watching THIS page for new steps. We are only here because you
  // clicked the extension on it.
  watchForSteps();
});
