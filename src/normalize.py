"""Map raw adapter records into the common schema + a dedupe hash."""
import hashlib
import re
import html as _html
from bs4 import BeautifulSoup


def _clean(text: str | None) -> str:
    return (text or "").strip().lower()


def _norm_title(t):
    t = _clean(t)
    t = re.sub(r"\(.*?\)", " ", t)  # parentheticals hatao
    t = re.sub(r"\b(remote|hybrid|on-?site|wfh|work from home)\b", " ", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)  # punctuation hatao
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _norm_company(c):
    c = _clean(c)
    for suf in (" inc", " inc.", " llc", " ltd", " ltd.", " corp", " corporation", " co", " gmbh", " limited"):
        if c.endswith(suf):
            c = c[:-len(suf)]
    return c.strip()


def dedupe_hash(company, title, location=None):
    raw = f"{_norm_company(company)}|{_norm_title(title)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Salary ──────────────────────────────────────────────────────────────────
#
# Boards publish pay in whatever shape they like: a structured min/max, a summary
# string ("$100k – $140k"), a range with currency symbols and thousands
# separators, or an hourly rate. This turns any of them into two integers, or
# None — and None is the honest answer when there is nothing to parse. A wrong
# salary is worse than no salary: profile.yaml's `salary_floor` filters on it.

_MONEY = re.compile(r"(\d[\d,\.]*)\s*([kK])?")

# Below this, a number in a job posting is a year, a req id, or an hourly rate —
# not an annual salary.
MIN_PLAUSIBLE_SALARY = 10_000


def _to_amount(number: str, k_suffix: str | None) -> int | None:
    try:
        value = float(number.replace(",", ""))
    except ValueError:
        return None
    if k_suffix:
        value *= 1000
    # A stray year ("Posted 2026") or a reference number is not pay. Nothing below
    # a plausible annual salary is trusted — better no figure than a wrong one,
    # since `salary_floor` filters jobs out on the strength of it.
    if value < MIN_PLAUSIBLE_SALARY:
        return None
    return int(value)


def parse_salary(raw) -> tuple[int | None, int | None]:
    """Best-effort (min, max) from whatever a board gave us.

    Accepts a dict with explicit min/max, a number, or a string to be read.
    Returns (None, None) rather than a guess when it cannot tell.
    """
    if raw is None:
        return None, None

    # Already structured.
    if isinstance(raw, dict):
        lo = raw.get("min") or raw.get("minValue") or raw.get("salary_min")
        hi = raw.get("max") or raw.get("maxValue") or raw.get("salary_max")
        lo = int(lo) if isinstance(lo, (int, float)) and lo >= MIN_PLAUSIBLE_SALARY else None
        hi = int(hi) if isinstance(hi, (int, float)) and hi >= MIN_PLAUSIBLE_SALARY else None
        return lo, hi

    if isinstance(raw, (int, float)):
        v = int(raw) if raw >= MIN_PLAUSIBLE_SALARY else None
        return v, v

    text = str(raw).strip()
    if not text:
        return None, None

    # Hourly rates aren't comparable to an annual floor — don't pretend.
    if re.search(r"\b(per\s*hour|/\s*h(ou)?r|hourly)\b", text, re.I):
        return None, None

    amounts = [a for a in (_to_amount(n, k) for n, k in _MONEY.findall(text)) if a]
    if not amounts:
        return None, None
    if len(amounts) == 1:
        return amounts[0], amounts[0]
    return min(amounts), max(amounts)


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"<[^>]+>", " ", text).replace("&nbsp;", " ").strip()


ALLOWED = {"h1", "h2", "h3", "h4", "p", "ul", "ol", "li", "strong", "b", "em", "i", "br", "a"}


def clean_html(raw: str | None) -> str:
    if not raw:
        return ""
    # double-encoded HTML (&lt;div&gt;) ko pehle decode karo
    if "&lt;" in raw and "<" not in raw:
        raw = _html.unescape(raw)

    soup = BeautifulSoup(raw, "html.parser")

    # junk tags poori tarah hatao
    for t in soup(["script", "style", "img", "svg", "iframe", "noscript"]):
        t.decompose()

    # disallowed tags unwrap; allowed pe sirf href rakho
    for tag in soup.find_all(True):
        if tag.name not in ALLOWED:
            tag.unwrap()
        else:
            tag.attrs = {k: v for k, v in tag.attrs.items() if k == "href"}

    # saari headings ko ek consistent size do (h1/h2 -> h3)
    for tag in soup.find_all(["h1", "h2"]):
        tag.name = "h3"

    # khaali blocks (sirf space / &nbsp;) hatao
    for tag in soup.find_all(["p", "li", "h3", "h4", "ul", "ol"]):
        if not tag.get_text(strip=True).replace("\xa0", ""):
            tag.decompose()

    out = str(soup).replace("\xa0", " ")
    out = re.sub(r"[ \t]{2,}", " ", out)  # extra spaces
    out = re.sub(r"(\s*<br\s*/?>\s*){2,}", "<br>", out)  # multiple <br> collapse
    out = re.sub(r"\n{2,}", "\n", out)  # extra newlines
    return out.strip()


def normalize(raw: dict) -> dict:
    company = raw.get("company")
    title = raw.get("title")
    location = raw.get("location")

    # Adapters may give an explicit pair, or a string to be read, or nothing.
    salary_min = raw.get("salary_min")
    salary_max = raw.get("salary_max")
    if salary_min is None and salary_max is None:
        salary_min, salary_max = parse_salary(raw.get("salary"))

    return {
        "dedupe_hash": dedupe_hash(company, title, location),
        "source": raw.get("source"),
        "scope": raw.get("scope", "regional"),
        "source_url": raw.get("source_url"),
        "apply_url": raw.get("apply_url") or raw.get("source_url"),
        "title": title,
        "company": company,
        "location": location,
        "remote": 1 if location and "remote" in location.lower() else 0,
        "description": clean_html(raw.get("description")),
        "posted_date": raw.get("posted_date"),
        "job_type": raw.get("job_type") or "Unknown",
        "deadline": raw.get("deadline"),
        "salary_min": salary_min,
        "salary_max": salary_max,
    }


MANDATORY = ("source", "apply_url", "title", "company",
             "location", "description")


def is_valid(job: dict) -> bool:
    """Mandatory fields present aur non-empty hain?"""
    for f in MANDATORY:
        v = job.get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            return False
    return True
