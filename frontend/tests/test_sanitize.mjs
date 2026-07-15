/**
 * The one place scraped, untrusted HTML reaches the DOM.
 *
 * Job descriptions come from job boards and are rendered with x-html. formatJD used
 * to pass them through raw whenever they already looked like HTML, so a posting
 * containing
 *     <img src=x onerror="fetch('/api/maint/nuclear',{method:'POST'})">
 * would run in the logged-in browser, with the user's session. Stored XSS: the
 * payload sits in the database and fires on every view, and the auth gate does not
 * help because the victim IS the authenticated user.
 *
 * This proves the sanitizer strips every attribute and every disallowed tag while
 * keeping the formatting and the text. It is the repo's one JavaScript test, run
 * with:  node frontend/tests/test_sanitize.mjs   (or: npm test)
 */
import { JSDOM } from 'jsdom';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const here = dirname(fileURLToPath(import.meta.url));
const appjs = join(here, '..', 'app.js');

// A browser-like environment for the sanitizer's DOMParser.
const dom = new JSDOM('<!DOCTYPE html><body></body>');
global.DOMParser = dom.window.DOMParser;
global.document = dom.window.document;
global.Node = dom.window.Node;

// Pull the three formatting methods out of app.js and evaluate them in isolation, so
// the test exercises the real shipped code rather than a copy.
const src = readFileSync(appjs, 'utf8');
const start = src.indexOf('formatJD(text) {');
const afterSan = src.indexOf('sanitizeHTML(dirty) {', start);
const end = src.indexOf('\n    },', afterSan) + '\n    },'.length;
if (start < 0 || afterSan < 0) {
  console.error('FAIL: could not find formatJD/sanitizeHTML in app.js — did they move?');
  process.exit(2);
}
const jd = eval(`({ ${src.slice(start, end)} })`);

let failures = 0;
const check = (name, cond) => {
  console.log(`  ${cond ? 'ok  ' : 'FAIL'}  ${name}`);
  if (!cond) failures++;
};

const isDangerous = (html) =>
  /<script|<img|<svg|<iframe|<object|<embed|<a\b|on\w+\s*=|javascript:|style\s*=/i
    .test(html);

// ── Every attribute-borne and tag-borne vector must not survive ────────────────
const ATTACKS = {
  'an onerror on an img':         '<div>Role<img src=x onerror="hack()"></div>',
  'a bare script tag':            '<p>Hi</p><script>hack()<\/script>',
  'an iframe with a js: src':     '<div>x</div><iframe src="javascript:hack()"></iframe>',
  'an anchor with a js: href':    '<p>Apply <a href="javascript:hack()">here</a></p>',
  'an svg onload':                '<div><svg onload="hack()"></svg>Text</div>',
  'an onmouseover handler':       '<div onmouseover="hack()">Hover</div>',
  'a payload nested two deep':    '<div><section><img src=x onerror="hack()"></section></div>',
  'a style with a js: url':       '<div style="background:url(javascript:hack())">x</div>',
  'an event on an allowed tag':   '<p onclick="hack()">Click</p>',
};
for (const [name, payload] of Object.entries(ATTACKS)) {
  check(`strips ${name}`, !isDangerous(jd.formatJD(payload)));
}

// ── The formatting we actually want must be preserved ──────────────────────────
const legit = jd.formatJD(
  '<p>We need a <strong>Python</strong> developer.</p><ul><li>Django</li><li>REST</li></ul>');
check('keeps <strong>', /<strong>/.test(legit));
check('keeps <li>',     /<li>/.test(legit));
check('keeps <p>',      /<p>/.test(legit));

// ── Text content is never lost, even when a tag is dropped ─────────────────────
const kept = jd.formatJD('<div>Great role <a href="javascript:hack()">apply now</a></div>');
check('keeps text from inside a dropped tag', /apply now/.test(kept));

// ── Plain text still gets laid out as before ───────────────────────────────────
const plain = jd.formatJD('REQUIREMENTS:\nPython and SQL\nGit');
check('plain text: heading detected', /<h4>REQUIREMENTS<\/h4>/.test(plain));
check('plain text: body wrapped in <p>', /<p>Python and SQL<\/p>/.test(plain));

// ── Empty / missing ────────────────────────────────────────────────────────────
check('empty input is handled', jd.formatJD('') === 'No description available.');

console.log(`\n  ${failures === 0 ? 'ALL PASS' : failures + ' FAILED'}`);
process.exit(failures === 0 ? 0 : 1);
