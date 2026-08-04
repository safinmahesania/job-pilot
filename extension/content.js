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
// on page load, and never on a page you didn't point it at.
//
// Guard against running twice in one document — but a plain "already loaded, do nothing"
// guard is a trap on a single-page app like Workday: after the extension is reloaded,
// clicking it injects the NEW script, but the OLD one already set the flag, so the new
// code would bail and the stale code would keep running (and keep throwing "context
// invalidated"). So the flag carries a version. A newer script signals the old one to
// stand down (its observer/timers check window.__jobpilotActive) and then takes over.
const JOBPILOT_VERSION = "1.9.7";
if (window.__jobpilotVersion && window.__jobpilotVersion !== JOBPILOT_VERSION) {
  // A different build was here — tell it to stop, then let this one proceed.
  window.__jobpilotActive = false;
  window.__jobpilotTookOver = true;
}
window.__jobpilotVersion = JOBPILOT_VERSION;

// A one-line banner in the console, so which build is actually running can be confirmed
// at a glance — the commonest cause of "the fix didn't work" is an extension that was
// edited on disk but never reloaded, still running the old code. If this line doesn't
// show the expected version after a fill, the reload didn't take.
console.log("[JobPilot] content script v" + JOBPILOT_VERSION + " loaded"
  + (window.__jobpilotTookOver ? " (took over from an older build — this page is fine now)" : ""));
window.__jobpilotActive = true;

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
  // Must be "full/legal/preferred/your name" — NOT a bare "name". Workday's fields are
  // "companyName", "schoolName", "fileName"; a lone "name" match was dropping the
  // applicant's legal name into the employer and school boxes.
  ["full_name",           /\b(full|legal|preferred|your|display)[\s_-]*name\b|\blegal\s*name\b/i],
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
  ["current_company",     /\b(current|present)?[\s_-]*(employer|company)(\s*name)?\b/i],
  ["job_description",     /\b(role|job)\s*description\b|\bresponsibilities\b/i],
  ["current_title",       /\b(current)?[\s_-]*(job\s*title|position|role)\b/i],
  ["job_location",        /\bwork\s*experience[\s\S]{0,20}\blocation\b|\bemployment[\s\S]{0,20}\blocation\b/i],
  ["school",              /\b(school|university|college|institution)(\s*name)?\b/i],
  ["degree",              /\bdegree\b/i],
  ["field_of_study",      /\b(field of study|major|discipline)\b/i],
  ["skills",              /\b(skills?|competenc(?:y|ies)|areas? of expertise)\b/i],
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

let answers = null;
let repeatedData = null;      // canonical answers from the API
let custom = [];         // the user's own keyword -> answer rules
let settings = { enabled: true, autoFill: false, useAI: true, jobId: null };
let filling = false;     // guards against re-entrant fills
let _contextDead = false; // set once the extension is torn down (reloaded), to stop retrying
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
  // A button-dropdown holds its choice as text, not .value. "Select One" / "Select a
  // value" is Workday's placeholder, not a real choice, so treat it as empty and fill it.
  if (el.tagName === "BUTTON" || el.getAttribute("aria-haspopup")) {
    const t = (el.textContent || "").trim().toLowerCase();
    return !!t && !/^select(\s|$)|select one|select a value|choose/i.test(t);
  }
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
    const legend = group.querySelector("legend, label, .field-label, h2, h3, h4");
    if (legend) bits.push(legend.innerText);
  }

  // The automation ids themselves, not just the text inside them. Workday names its
  // hooks after what they are — resumeUpload, fileUploadDropZone — while the visible
  // words can sit in a sibling this element has no relationship to. The attribute is
  // often the only thing that says what the control is for, and reading it costs
  // nothing: camelCase is split so "resumeUpload" reads as "resume Upload" and the
  // patterns below can see the word.
  for (let node = el, hops = 0; node && hops < 4; node = node.parentElement, hops++) {
    const id = node.getAttribute && node.getAttribute("data-automation-id");
    if (id) bits.push(id.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[-_]/g, " "));
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
// ── Repeated sections ───────────────────────────────────────────────────────
//
// A form that lets you press "Add" three times has three Job Title boxes, three
// Employer boxes, and so on. Every one of them carries the same label, so a lookup
// by label answers all three with the same job — which is what happened: three
// identical work histories, and the same for education.
//
// Nothing in the label distinguishes them, so position does. The n-th Employer box
// on the page belongs to the n-th job in your history: forms render repeats in
// order, and so does profile.yaml. Counting per key rather than per section avoids
// having to understand each site's DOM, which is where this would otherwise turn
// into guesswork about Workday's markup specifically.
//
// When your history runs out, the remaining boxes are left alone. An empty third
// section is honest; a third copy of your second job is not.

const REPEAT_KEYS = {
  current_company: ["experience", "company"],
  current_title:   ["experience", "title"],
  job_location:    ["experience", "location"],
  job_start:       ["experience", "start"],
  job_end:         ["experience", "end"],
  job_description: ["experience", "description"],
  school:          ["education", "school"],
  degree:          ["education", "degree"],
  field_of_study:  ["education", "field"],
  graduation_year: ["education", "end"],
  edu_start:       ["education", "start"],
};

/** How many times each repeatable key has been filled during this pass. */
let repeatSeen = {};

function resetRepeats() {
  repeatSeen = {};
}

/**
 * The value for `key` at its next occurrence, or undefined to fall through to the
 * flat answer. Returns null when the history has run out, meaning "leave it empty".
 */
function repeatedValue(key) {
  const spec = REPEAT_KEYS[key];
  if (!spec || !repeatedData) return undefined;
  const [listName, field] = spec;
  const list = repeatedData[listName] || [];
  if (!list.length) return undefined;

  const i = repeatSeen[key] || 0;
  repeatSeen[key] = i + 1;

  if (i >= list.length) return null;      // nothing left to say — leave it blank
  return list[i][field] || null;
}

function collectFields() {
  // input/select/textarea, plus the button-dropdowns Workday uses for Degree, Country,
  // Province and Phone Type — those are <button aria-haspopup>, not <select>, so without
  // them here their handler never runs.
  const nodes = document.querySelectorAll(
    "input, select, textarea, button[aria-haspopup], "
    + '[role="combobox"], button[data-automation-id*="Prompt" i]');
  return Array.from(nodes).filter((el) => {
    if (SKIP_TYPES.has(el.type)) return false;
    if (!isVisible(el)) return false;
    if (hasValue(el)) return false;             // never overwrite existing input
    return true;
  });
}


// ── Skills typeahead ────────────────────────────────────────────────────────
//
// Workday-style skill fields are a text input wired to a dropdown: you type, a list
// appears, you click a match, and it becomes a removable tag. The value never lives in
// the input's own .value — it lives in the tags — so the ordinary "set value, dispatch
// change" does nothing useful, and pasting the comma-joined list makes one tag with
// every skill mashed together.
//
// This types each skill, waits for the dropdown, and picks the option that matches. It
// is best-effort: if the dropdown doesn't appear or has no match, that skill is skipped
// rather than forced, because a wrong tag is worse than a missing one on a form a human
// will review.

function _looksLikeTypeahead(el) {
  // A text input that is also a combobox, or sits inside one. Plain text inputs and
  // real <select>s are handled elsewhere; this is only the type-and-pick control.
  if (!el || el.tagName !== "INPUT") return false;
  const type = (el.type || "text").toLowerCase();
  if (type !== "text" && type !== "search") return false;
  const role = (el.getAttribute("role") || "").toLowerCase();
  const owns = el.getAttribute("aria-autocomplete") || el.getAttribute("aria-controls");
  // Workday's multiselect (skills, etc.) puts no aria on the input itself — the input is
  // a bare <input placeholder="Search"> inside a .multiSelectContainer /
  // multiselectInputContainer. Match that container by class OR automation-id, since the
  // id may be absent on some tenants.
  const inCombo = el.closest(
    '[role="combobox"], [data-automation-id*="multiSelect" i], [data-automation-id*="skill" i], '
    + '.multiSelectContainer, [class*="multiselect" i]');
  return role === "combobox" || !!owns || !!inCombo;
}

/** Is this the skills field? True when the label mentions skills, or when it's a
 *  multiselect typeahead on a page that has skills to add and no better candidate — the
 *  Workday skills control carries no "skill" text on the input, only a "Search"
 *  placeholder inside a multiSelectContainer. */
function _isSkillsField(el, labelText) {
  if (/\b(skills?|competenc(?:y|ies)|areas? of expertise|expertise)\b/i.test(labelText)) {
    return true;
  }
  // No skill word anywhere near it — fall back to the container shape, but only for a
  // real multiselect (so a plain "Search" box elsewhere isn't mistaken for skills).
  const container = el.closest('.multiSelectContainer, [class*="multiselect" i], '
    + '[data-automation-id*="multiSelect" i]');
  return !!container && (el.placeholder || "").trim().toLowerCase() === "search";
}

function _sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

function _typeInto(el, text) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
  if (setter) setter.call(el, text); else el.value = text;
  // React tracks the value through its own setter and re-reads on input; fire input plus
  // a keyup so autocompletes that listen for either see the change.
  el.dispatchEvent(new Event("input", { bubbles: true }));
  const last = text.slice(-1) || "";
  el.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: last }));
  el.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true, key: last }));
}

function _openOptions() {
  // The dropdown options, wherever the framework renders them — usually a listbox
  // appended to the body rather than a sibling of the input. Workday's multiselect menu
  // uses menuItem/promptOption automation-ids and sometimes plain [role=option]; cover
  // the common shapes so a match can be found whatever the tenant renders.
  return Array.from(document.querySelectorAll(
    '[role="option"], [role="listbox"] li, [data-automation-id*="promptOption" i], '
    + '[data-automation-id*="menuItem" i], [data-automation-id*="searchResult" i], '
    + 'ul[role="listbox"] [role="option"]'
  )).filter((o) => o.offsetParent !== null && o.textContent.trim());
}

function _pressEnter(el) {
  // Workday's skills box only runs its search when you press Enter — typing alone shows
  // nothing. Fire a full key sequence so the framework's keydown handler sees it.
  for (const type of ["keydown", "keypress", "keyup"]) {
    el.dispatchEvent(new KeyboardEvent(type, {
      bubbles: true, cancelable: true, key: "Enter", code: "Enter", keyCode: 13, which: 13,
    }));
  }
}

async function _fillSkillsTypeahead(el, skills) {
  let added = 0;
  console.log(`[JobPilot] skills: trying ${Math.min(skills.length, 20)} skills`);
  for (const skill of skills.slice(0, 20)) {         // a sane cap for one field
    try {
      // Re-find the input each round: after a tag is added Workday can replace the input
      // node, so a stale reference would type into nothing.
      const input = _currentSkillInput(el) || el;
      input.focus();
      _typeInto(input, skill);
      await _sleep(150);

      // This field searches on Enter, not on keystroke. Press it, then wait for the
      // results list to appear.
      _pressEnter(input);
      let opts = [];
      for (let waited = 0; waited < 2500 && !opts.length; waited += 150) {
        await _sleep(150);
        opts = _openOptions();
      }
      if (!opts.length) {                            // nothing came back — clear and move on
        console.log(`[JobPilot] skills: "${skill}" — no options appeared`);
        _typeInto(input, "");
        continue;
      }

      const want = skill.toLowerCase();
      const match =
        opts.find((o) => o.textContent.trim().toLowerCase() === want) ||
        opts.find((o) => o.textContent.trim().toLowerCase().startsWith(want)) ||
        opts.find((o) => o.textContent.trim().toLowerCase().includes(want));
      if (!match) {
        console.log(`[JobPilot] skills: "${skill}" — ${opts.length} options but no match:`,
                    opts.slice(0, 4).map((o) => o.textContent.trim()));
        _typeInto(input, "");
        continue;
      }

      match.click();
      added++;
      await _sleep(200);                             // let the tag render and the input reset
    } catch (e) {
      console.warn(`[JobPilot] skills: "${skill}" threw`, e);
    }
  }
  console.log(`[JobPilot] skills: added ${added}`);
  return added;
}

/** The live skills input — re-queried because adding a tag can swap the node out. */
function _currentSkillInput(seed) {
  const container = seed.closest(
    '.multiSelectContainer, [class*="multiselect" i], [data-automation-id*="multiSelect" i]');
  if (!container) return seed;
  const input = container.querySelector('input[type="text"], input:not([type])');
  return input || seed;
}


// ── Workday split date inputs ───────────────────────────────────────────────
//
// Workday's dates are two spinbuttons — a Month and a Year — and it renders one such
// pair for the start and another for the end of each experience. Worse, every one of
// them shares the same id ("dateSectionMonth-input"), so they can't be told apart by id
// at all; only their order on the page and their aria-label ("Month" vs "Year")
// distinguish them. The generic key logic can't see any of that, so left to the AI pass
// they get filled with nonsense (a month in the year box, a "1" for a year).
//
// So they are handled on their own: walk the experience blocks in document order, and
// for each block fill its two date pairs (start, then end) from that experience's dates.
// A date is "YYYY-MM"; the year and month go to their labelled inputs. An experience
// with no end date leaves the end pair blank.

function _experienceBlocks() {
  // The wrapper for each experience, in the order they appear. Workday numbers the ids
  // (workExperience-143--jobTitle), so group by that number.
  const seen = new Map();
  document.querySelectorAll('[data-automation-id^="workExperience-"]').forEach((el) => {
    const m = (el.getAttribute("data-automation-id") || "").match(/workExperience-(\d+)--/);
    if (!m) return;
    if (!seen.has(m[1])) {
      seen.set(m[1], el.closest('[data-automation-id*="workExperience" i]') || el.parentElement);
    }
  });
  return [...seen.values()];
}

function _setSpin(input, value) {
  // Spinbuttons validate keystrokes, so set through the native setter and fire the
  // events Workday listens for, same as any other controlled input.
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, "value")?.set;
  if (setter) setter.call(input, String(value)); else input.value = String(value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  input.dispatchEvent(new Event("blur", { bubbles: true }));
}

function _fillDatePair(container, ym) {
  // ym is "YYYY-MM". Find the Month and Year spinbuttons inside this container and set
  // them. Returns true if it put something in.
  const m = /^(\d{4})-(\d{1,2})/.exec(ym || "");
  if (!m) return false;
  const year = m[1];
  const month = String(parseInt(m[2], 10));

  const spins = [...container.querySelectorAll(
    'input[aria-label="Month"], input[aria-label="Year"], input[data-automation-id*="date" i]')];
  const monthEl = spins.find((s) =>
    /month/i.test(s.getAttribute("aria-label") || s.getAttribute("data-automation-id") || ""));
  const yearEl = spins.find((s) =>
    /year/i.test(s.getAttribute("aria-label") || s.getAttribute("data-automation-id") || ""));
  let did = false;
  if (monthEl) { _setSpin(monthEl, month); did = true; }
  if (yearEl) { _setSpin(yearEl, year); did = true; }
  return did;
}

function fillWorkdayDates() {
  const experience = repeatedData?.experience || [];
  if (!experience.length) return 0;

  const blocks = _experienceBlocks();
  let filled = 0;
  blocks.forEach((block, i) => {
    const exp = experience[i];
    if (!exp) return;

    // Each block has a start section and (unless the job is current) an end section.
    // Match them by a start/end or from/to wrapper when Workday labels them; otherwise
    // fall back to the two date sections in order.
    const groups = [];
    const startWrap = block.querySelector(
      '[data-automation-id*="startDate" i], [data-automation-id*="from" i]');
    const endWrap = block.querySelector(
      '[data-automation-id*="endDate" i], [data-automation-id*="to" i]');
    if (startWrap) groups.push(["start", startWrap]);
    if (endWrap) groups.push(["end", endWrap]);

    if (!groups.length) {
      const sections = [...block.querySelectorAll('[data-automation-id*="dateSection" i]')]
        // collapse to distinct date wrappers, not each spinbutton
        .map((s) => s.closest('[data-automation-id*="formField" i], [data-automation-id*="date" i]') || s)
        .filter((v, idx, arr) => arr.indexOf(v) === idx);
      if (sections[0]) groups.push(["start", sections[0]]);
      if (sections[1]) groups.push(["end", sections[1]]);
    }

    for (const [which, wrap] of groups) {
      const val = which === "start" ? exp.start : exp.end;
      if (val && _fillDatePair(wrap, val)) filled++;
    }

    // "I currently work here" defaults to ticked in Workday, but a job with an end date
    // is not current — an unchecked-by-default form would leave every past job claiming
    // to be present employment. Untick it where we have an end date; leave it alone
    // (the user decides) when the job genuinely has none.
    if (exp.end) {
      const current = block.querySelector('input[type="checkbox"][data-automation-id*="currentlyWorkHere" i], input[type="checkbox"][id*="currentlyWorkHere" i]');
      if (current && current.checked) { current.click(); filled++; }
    }
  });
  return filled;
}


async function fillPage({ silent = false } = {}) {
  if (filling) return { filled: 0, skipped: 0 };
  filling = true;

  try {
    if (!answers) {
      const res = await send({ type: "getAnswers" });
      if (res?.reason === "context-invalidated") {
        // send() already toasted the refresh hint; just stop.
        return { filled: 0, skipped: 0 };
      }
      if (!res?.ok) {
        if (!silent) toast("Can't reach JobPilot — is it running?", "error");
        return { filled: 0, skipped: 0 };
      }
      answers = res.data.answers;
      custom = res.data.custom || [];
      repeatedData = res.data.repeated || null;
    }

    const fields = collectFields();
    resetRepeats();          // positions are per pass, not per page lifetime
    console.log(`[JobPilot] collectFields found ${fields.length} field(s)`,
                fields.slice(0, 30).map((f) => f.getAttribute("data-automation-id") || f.id || f.name || f.tagName));
    if (!fields.length) {
      if (!silent) toast("Nothing to fill on this page", "info");
      return { filled: 0, skipped: 0 };
    }

    let filled = 0;
    const unresolved = [];

    // Workday's split date spinbuttons are handled on their own, before the generic
    // loop, because they can't be told apart by id and the AI pass fills them with
    // nonsense. Wrapped in its own try/catch: a date-fill that throws must not take the
    // rest of the form down with it — better a filled form with blank dates than a page
    // where nothing filled at all.
    try {
      filled += fillWorkdayDates();
    } catch (e) {
      console.warn("[JobPilot] date fill failed, continuing:", e);
    }

    // Pass 1 — your own custom rules first, then the built-in heuristics.
    for (const el of fields) {
      // A date spinbutton was just handled (or intentionally left blank) above — never
      // let the generic logic or the AI pass touch it, or a month lands in the year box.
      const autoId = el.getAttribute("data-automation-id") || "";
      const aria = el.getAttribute("aria-label") || "";
      if (/dateSection/i.test(autoId) || (el.getAttribute("role") === "spinbutton"
          && /month|year|day/i.test(aria))) {
        continue;
      }

      const text = labelFor(el);

      // Your explicit answers win over everything else.
      const mine = matchCustom(text);
      if (mine && await applyValue(el, mine)) { filled++; continue; }

      const key = matchKey(text);

      // A skills field is often a typeahead: you type a skill, pick it from a dropdown,
      // and it becomes a tag; then the next one. Pasting the whole comma-joined list
      // into it enters one nonsense "skill" called "Python, SQL, React, ...". So when
      // this looks like the skills control and the page has skills to add, add them one
      // at a time. Recognised by shape as well as label, because Workday's skills input
      // carries no "skill" text — just a "Search" placeholder in a multiSelectContainer.
      if (_isSkillsField(el, text) && _looksLikeTypeahead(el) && repeatedData?.skills?.length) {
        const added = await _fillSkillsTypeahead(el, repeatedData.skills);
        if (added) { filled++; continue; }
        continue;    // it's the skills box; don't let generic logic type into it
      }

      if (key) {
        // A repeatable key answers by position first: the second Employer box gets
        // the second job, not a second copy of the first.
        const nth = repeatedValue(key);
        if (nth === null) continue;                 // history exhausted — leave blank
        const value = nth !== undefined ? nth : asAnswer(key, answers[key]);
        // Blank profile value: leave the field alone. Voluntary questions are
        // never guessed at.
        if (!value) {
          if (!VOLUNTARY_KEYS.has(key)) unresolved.push({ el, text });
          continue;
        }
        if (await applyValue(el, value)) { filled++; continue; }
      }

      // Checkboxes and radios the heuristics didn't claim are left for the human. The
      // AI pass fills free-text; letting it decide a yes/no it doesn't understand is how
      // "currently work here" ended up ticked on every past job at once. A wrong tick on
      // an application is worse than an empty one the person sets themselves.
      if (el.type === "checkbox" || el.type === "radio") continue;

      if (!VOLUNTARY_KEYS.has(key)) unresolved.push({ el, text });
    }
    // Three "Why do you want to work here?" boxes would be one question; three
    // "Job Title" boxes in three repeated sections are three. Number the repeats so
    // the cache below answers each one separately instead of pasting the first
    // answer into all of them.
    const labelCount = {};
    for (const item of unresolved) {
      const n = labelCount[item.text] = (labelCount[item.text] || 0) + 1;
      item.cacheKey = n === 1 ? item.text : `${item.text} #${n}`;
    }

    // Pass 2 — one batched AI call for whatever is left.
    if (settings.useAI && unresolved.length) {
      const pending = [];
      for (const item of unresolved) {
        const cached = aiCache.get(item.cacheKey);
        if (cached !== undefined) {
          if (cached && await applyValue(item.el, cached)) filled++;
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
          for (let i = 0; i < pending.length; i++) {
            const item = pending[i];
            const answer = mapped[`f${i}`] || "";
            aiCache.set(item.cacheKey, answer);   // remember, even if blank
            if (answer && await applyValue(item.el, answer)) filled++;
          }
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
async function applyValue(el, value) {
  try {
    if (el.tagName === "SELECT") return fillSelect(el, value);
    if (el.type === "radio") return fillRadio(el, value);
    if (el.type === "checkbox") {
      const yes = /^(yes|true|1)$/i.test(String(value));
      if (yes !== el.checked) el.click();
      return yes;
    }
    // Workday renders its dropdowns (Degree, Country, Province, Phone Type) as a
    // <button>, not a <select>: clicking it opens a listbox appended elsewhere in the
    // DOM, and you pick an option from there. A plain setValue does nothing to those, so
    // they need the open-and-choose dance.
    if (el.tagName === "BUTTON" || el.getAttribute("aria-haspopup")) {
      return await _fillButtonDropdown(el, value);
    }
    setValue(el, value);
    return true;
  } catch {
    return false;
  }
}

/** Open a Workday button-dropdown and click the option that matches `value`. */
async function _fillButtonDropdown(button, value) {
  const want = String(value).trim().toLowerCase();
  if (!want) return false;

  button.click();                                    // open the listbox
  let opts = [];
  for (let waited = 0; waited < 1500 && !opts.length; waited += 150) {
    await new Promise((r) => setTimeout(r, 150));
    opts = Array.from(document.querySelectorAll(
      '[role="option"], [role="listbox"] li, [data-automation-id*="promptOption" i]'
    )).filter((o) => o.offsetParent !== null && o.textContent.trim());
  }
  if (!opts.length) return false;

  const match =
    opts.find((o) => o.textContent.trim().toLowerCase() === want) ||
    opts.find((o) => o.textContent.trim().toLowerCase().includes(want)) ||
    opts.find((o) => want.includes(o.textContent.trim().toLowerCase()) && o.textContent.trim());
  if (!match) {
    // Nothing fits — close the menu (Escape) and leave it for the human, rather than
    // pick a wrong degree.
    button.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Escape" }));
    return false;
  }
  match.click();
  return true;
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
// Workday's upload input carries no "resume" text — its label is "Drop files here /
// Select files" and its automation-id is "attachments-FileUpload" / "file-upload-input-
// ref". On an application form a lone "attachments" upload is the resume slot, so these
// count too, which lets it be identified without relying on the single-input fallback.
const ATTACHMENT_PATTERN = /\b(attachment|file[\s_-]*upload|upload[\s_-]*file|drop files|select files)\b/i;
const COVER_PATTERN = /\b(cover[\s_-]*letter|covering[\s_-]*letter|motivation)\b/i;

/** Turn the base64 the service worker sent back into a File. */
function toFile({ name, base64, type }) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new File([bytes], name, { type });
}

/** Put a File into an <input type="file"> the way the page will notice.
 *
 * A hidden, framework-controlled input (Workday, and most modern ATSes) does not react
 * to `input.files = ...` alone. React tracks the input through a value setter it patched
 * onto the element, and it only re-reads `files` when a `change` bubbles up through its
 * own listener — which is attached at the document, so the event has to bubble, and the
 * element has to look like it was really interacted with. So: set files via the native
 * property, focus the element, and fire the full sequence a real pick produces. */
function attachTo(input, file) {
  const dt = new DataTransfer();
  dt.items.add(file);

  // Use the native setter so a framework that wrapped `files`/`value` still sees it.
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, "files")?.set;
  if (setter) setter.call(input, dt.files);
  else input.files = dt.files;

  try { input.focus(); } catch { /* hidden inputs may refuse focus; harmless */ }
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  // Some flows only commit the file when focus leaves the control.
  input.dispatchEvent(new Event("blur", { bubbles: true }));
  return input.files?.length === 1;
}

/** Every file input on the page, tagged with what it seems to want. */
function fileInputs() {
  return Array.from(document.querySelectorAll('input[type="file"]'))
    .filter((el) => !el.disabled && !el.files?.length)   // don't replace an upload
    .map((el) => {
      // The automation-id is part of the label here: Workday's file input is hidden
      // (display:none, styled button in front) and its visible text says only "Select
      // files", so the id "attachments-FileUpload" is often the only thing that names
      // what the control is for.
      const ids = [el.getAttribute("data-automation-id") || "",
                   el.closest("[data-automation-id]")?.getAttribute("data-automation-id") || ""];
      const text = labelFor(el) + " " + (el.getAttribute("accept") || "") + " " + ids.join(" ");
      let kind = null;
      if (COVER_PATTERN.test(text)) kind = "cover";        // check cover first:
      else if (RESUME_PATTERN.test(text)) kind = "resume"; // "resume or cover letter"
      else if (ATTACHMENT_PATTERN.test(text)) kind = "resume"; // a lone "attachments" box
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
    // One upload box and nothing in the page to say what it wants. The rule that
    // stops a cover letter landing in the resume slot is about telling two documents
    // apart — with a single box there is no second slot to get wrong, and every
    // application form with one upload is asking for a resume. Guessing here is safe
    // in a way that guessing between two boxes is not.
    if (inputs.length === 1) {
      inputs[0].kind = "resume";
      identified.push(inputs[0]);
    } else {
      toast(`${inputs.length} upload field(s) here, but I can't tell what they want — attach by hand`, "info");
      return { attached: 0 };
    }
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
  if (_contextDead || window.__jobpilotActive === false) return;  // gone, or superseded
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
    // After the extension is reloaded, an old content script left running on a page that
    // wasn't refreshed still tries to talk to a service worker that no longer exists.
    // chrome.runtime.sendMessage then reports "Extension context invalidated" — sometimes
    // by throwing synchronously right here, sometimes via chrome.runtime.lastError inside
    // the callback, and sometimes the callback simply never fires. All three are handled:
    // the try/catch for the throw, the lastError check for the callback, and _contextDead
    // so the observer and auto-fill stop calling in once we know the context is gone.
    if (_contextDead || window.__jobpilotActive === false || !chrome.runtime?.id) {
      resolve({ ok: false, reason: "context-invalidated" });
      return;
    }
    try {
      chrome.runtime.sendMessage(message, (response) => {
        const err = chrome.runtime.lastError;
        if (err) {
          if (String(err.message || err).includes("context invalidated")) {
            _markContextDead();
          }
          resolve({ ok: false });
          return;
        }
        resolve(response);
      });
    } catch (e) {
      if (String(e).includes("context invalidated")) _markContextDead();
      resolve({ ok: false, reason: "context-invalidated" });
    }
  });
}

function _markContextDead() {
  if (_contextDead) return;
  _contextDead = true;
  // The page is now running against a dead extension. Nothing here will work until the
  // page is refreshed, so stop the observer from firing more doomed calls and tell the
  // user once.
  try { toast("JobPilot was updated — refresh this page (Ctrl+Shift+R) to use it again", "error"); } catch { /* toast may be unavailable */ }
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
