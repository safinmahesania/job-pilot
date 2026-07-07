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
