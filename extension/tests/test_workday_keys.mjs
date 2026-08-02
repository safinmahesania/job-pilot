// Workday names its fields with data-automation-ids like "workExperience-9--jobTitle"
// and "education-48--degree". labelFor turns those into words ("work Experience 9 job
// Title") and matchKey maps them to a canonical key. This checks the mapping holds for
// the real ids seen on a Workday application, so a role description never gets read as a
// job title again.

const MATCHERS = [
  ["linkedin",            /\blinked-?in\b/i],
  ["github",              /\bgit-?hub\b/i],
  ["current_company",     /\b(current|present)?[\s_-]*(employer|company)(\s*name)?\b/i],
  ["job_description",     /\b(role|job)\s*description\b|\bresponsibilities\b/i],
  ["current_title",       /\b(current)?[\s_-]*(job\s*title|position|role)\b/i],
  ["job_location",        /\bwork\s*experience[\s\S]{0,20}\blocation\b|\bemployment[\s\S]{0,20}\blocation\b/i],
  ["school",              /\b(school|university|college|institution)(\s*name)?\b/i],
  ["degree",              /\bdegree\b/i],
  ["field_of_study",      /\b(field of study|major|discipline|field\s*of\s*study)\b/i],
  ["skills",              /\b(skills?|competenc(?:y|ies)|areas? of expertise)\b/i],
  ["city",                /\b(city|town|locality)\b/i],
];

function labelFromId(id) {
  return id.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/[-_]/g, " ");
}
function matchKey(text) {
  for (const [k, re] of MATCHERS) if (re.test(text)) return k;
  return null;
}

let failed = 0;
function expect(id, want) {
  const got = matchKey(labelFromId(id));
  const ok = got === want;
  if (!ok) failed++;
  console.log(`  ${ok ? "ok  " : "FAIL"} ${id.padEnd(36)} -> ${got} ${ok ? "" : "(wanted " + want + ")"}`);
}

// The real ids from a Workday "My Experience" page.
expect("workExperience-9--jobTitle", "current_title");
expect("workExperience-9--companyName", "current_company");
expect("workExperience-9--location", "job_location");
expect("workExperience-9--roleDescription", "job_description");   // NOT current_title
expect("education-48--schoolName", "school");
expect("education-48--degree", "degree");
expect("education-48--fieldOfStudy", "field_of_study");
expect("skills--skills", "skills");

if (failed) { console.log(`\n  ${failed} FAILED`); process.exit(1); }
console.log("\n  ALL PASS");

// ── Name fields must not swallow "companyName" / "schoolName" ──────────────────
// A bare "name" match was dropping the applicant's legal name into the employer and
// school boxes. full_name now needs a qualifier (full/legal/preferred/your/display).
const NAME_RULES = [
  ["first_name",  /\b(first|given)[\s_-]*name\b|^fname$/i],
  ["last_name",   /\b(last|family|sur)[\s_-]*name\b|^lname$/i],
  ["full_name",   /\b(full|legal|preferred|your|display)[\s_-]*name\b|\blegal\s*name\b/i],
  ["current_company", /\b(current|present)?[\s_-]*(employer|company)(\s*name)?\b/i],
  ["school",      /\b(school|university|college|institution)(\s*name)?\b/i],
];
function nameKey(text) {
  for (const [k, re] of NAME_RULES) if (re.test(text)) return k;
  return null;
}
let nameFailed = 0;
function expectName(id, want) {
  const got = nameKey(labelFromId(id));
  const ok = got === want;
  if (!ok) nameFailed++;
  console.log(`  ${ok ? "ok  " : "FAIL"} ${id.padEnd(36)} -> ${got} ${ok ? "" : "(wanted " + want + ")"}`);
}
console.log("\n  name-field disambiguation:");
expectName("name--legalName--firstName", "first_name");
expectName("name--legalName--lastName", "last_name");
expectName("workExperience-53--companyName", "current_company");  // NOT full_name
expectName("education-92--schoolName", "school");                 // NOT full_name

if (nameFailed) { console.log(`\n  ${nameFailed} NAME FAILED`); process.exit(1); }
console.log("\n  NAME ALL PASS");
