"""Is a posting written in English, French, or both?

Montreal is bilingual, so the feed mixes English postings, French postings, and ones that
run the same job in both languages. That matters twice over. A French-only posting scores
badly on an English-tuned fit filter not because the job is a poor match but because
"ingénieur logiciel" is not the string "software engineer" — so without knowing the
language, a good job looks like a bad one. And whether you can apply in French at all is
something you want to see at a glance, not discover three paragraphs in.

So each posting is tagged: "en", "fr", or "bilingual". The tag is shown in the feed and
travels with the job to scoring, where the model — which reads French perfectly well — is
told the language so it judges the role, not the vocabulary.

Detection is a word-frequency heuristic, not a library. It runs on every job in every run,
a dependency-free check is one less thing to install and break, and telling French from
English on a paragraph of text does not need a model. It leans on function words —
"le/la/des/vous/nous" against "the/and/you/with" — because those appear in any prose
regardless of subject, where a shared technical term like "Python" or "API" tells you
nothing about the language around it.
"""
from __future__ import annotations

import re

# Function words that are common in one language and absent (or rare) in the other. Kept
# to high-frequency grammar words, which show up in any prose — a job ad, a poem, a recipe
# — rather than topic words a bilingual tech posting would share.
_FRENCH = {
    "le", "la", "les", "un", "une", "des", "du", "de", "et", "ou", "vous", "nous",
    "votre", "notre", "avec", "pour", "dans", "sur", "est", "sont", "qui", "que",
    "ce", "cette", "ces", "au", "aux", "en", "par", "plus", "vos", "nos", "être",
    "sera", "poste", "entreprise", "emploi", "travail", "équipe", "expérience",
    "compétences", "connaissances", "responsabilités", "exigences", "atouts",
}
_ENGLISH = {
    "the", "a", "an", "and", "or", "you", "we", "your", "our", "with", "for", "in",
    "on", "is", "are", "who", "that", "this", "these", "at", "by", "more", "will",
    "be", "job", "role", "company", "team", "experience", "skills", "knowledge",
    "responsibilities", "requirements", "position", "work", "as", "of", "to",
}

_WORD = re.compile(r"[a-zàâäéèêëïîôöùûüçœ]+", re.I)

#: Below this share, one language is treated as incidental — a stray English word in a
#: French ad, or the reverse — rather than the posting being genuinely in both. A real
#: bilingual posting repeats whole sections, so its minority language is well above this.
_BILINGUAL_FLOOR = 0.25

#: Too few function words to tell. Very short snippets (a title and a line) fall here, and
#: are left as unknown rather than guessed at.
_MIN_SIGNAL = 4


def detect(text: str | None) -> str:
    """One of "en", "fr", "bilingual", or "unknown".

    "unknown" when there is too little text to judge — the caller treats that as "don't
    filter on language", which keeps a thin posting in the feed rather than dropping it on
    a guess.
    """
    if not text:
        return "unknown"

    words = [w.lower() for w in _WORD.findall(text)]
    fr = sum(1 for w in words if w in _FRENCH)
    en = sum(1 for w in words if w in _ENGLISH)
    total = fr + en

    if total < _MIN_SIGNAL:
        return "unknown"

    fr_share = fr / total
    en_share = en / total

    # Both languages well represented — the posting runs in both.
    if fr_share >= _BILINGUAL_FLOOR and en_share >= _BILINGUAL_FLOOR:
        return "bilingual"
    return "fr" if fr_share > en_share else "en"


def is_french_only(text: str | None) -> bool:
    """A posting a French-only reader could apply to and an English-only reader could
    not. Used to flag jobs in the feed."""
    return detect(text) == "fr"


#: Shown in the UI. "unknown" and "en" carry no badge — English is the default and an
#: undetected language is not worth a label.
BADGE = {"fr": "FR only", "bilingual": "FR / EN"}
