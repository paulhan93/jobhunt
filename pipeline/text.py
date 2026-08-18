"""Shared text-normalization utilities used across pipeline stages. Previously
duplicated: pipeline/fetch.py and pipeline/extract.py each had their own
strip_html(), and pipeline/fetch.py and pipeline/filters.py each had their own
"is this text talking about remote work" regex. One copy of each here instead,
imported everywhere it's needed, so a future fix (a new HTML quirk, a new
remote-phrasing variant) only has to happen once.
"""
import html
import re

_TAG = re.compile(r"<[^>]+>")
_SMART_PUNCT = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-",
})

# "Is this text talking about remote work", used both at ingestion
# (pipeline/fetch.py, populates jobs.remote) and at filter time
# (pipeline/filters.py, re-derives remote-ness from location text since the
# ATS remote flag alone isn't trustworthy, PROJECT.md §5). One pattern.
REMOTE_RE = re.compile(r"\bremote\b|\banywhere\b|\bdistributed\b|\bwork from home\b", re.I)


def strip_html(raw: str | None) -> str | None:
    """Plain text from a raw ATS description: strip tags, unescape entities,
    normalize typographic punctuation (curly quotes/dashes) to straight
    equivalents, collapse whitespace."""
    if not raw:
        return None
    text = _TAG.sub(" ", raw)
    text = html.unescape(text)
    text = text.translate(_SMART_PUNCT)
    return re.sub(r"\s+", " ", text).strip() or None
