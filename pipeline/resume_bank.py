"""Single I/O boundary for resume.yaml (PROJECT.md §8). Previously,
pipeline/score.py, pipeline/extract.py, and pipeline/tailor.py each opened and
parsed resume.yaml independently, a single `python -m scripts.tailor` run
parsed the file from disk three separate times. One cached load here instead;
every other module derives its own view (bullet bank, skill-years, total
years, the full dict) from this one parse.

Cached per process with lru_cache, correct for the one-shot CLI scripts this
project runs today, where resume.yaml never changes mid-run. If this is ever
called from a long-lived process (the "someday" frontend in PROJECT.md's
backlog), this cache needs an explicit invalidation story, since resume.yaml
could be hand-edited between requests in that world.
"""
from datetime import date
from functools import lru_cache

import yaml

# personal/ holds resume.yaml, the real company list, and the probe
# checkpoint, everything gitignored and specific to one person, kept
# together and out of the repo root (2026-08-17 reorg, DECISIONS.md #80).
DEFAULT_PATH = "personal/resume.yaml"


@lru_cache(maxsize=1)
def load(path: str = DEFAULT_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _years_between(start: str, end: str | None) -> float:
    sy, sm = (int(p) for p in start.split("-"))
    if end:
        ey, em = (int(p) for p in end.split("-"))
    else:
        today = date.today()
        ey, em = today.year, today.month
    return (ey - sy) + (em - sm) / 12


def skill_years(path: str = DEFAULT_PATH) -> dict[str, float]:
    """Sum years per skill_key across experience roles (not projects, years
    of experience means employment) where at least one bullet carries the tag."""
    data = load(path)
    years: dict[str, float] = {}
    for role in data.get("experience", []):
        role_years = _years_between(role["start"], role.get("end"))
        tags = {t for b in role.get("bullets", []) for t in b.get("tags", [])}
        for tag in tags:
            years[tag] = years.get(tag, 0.0) + role_years
    return years


def total_years(path: str = DEFAULT_PATH) -> float:
    """Total professional (experience-only) years, for years-checks whose
    requirement has no skill_key."""
    data = load(path)
    return sum(
        _years_between(role["start"], role.get("end"))
        for role in data.get("experience", [])
    )


def bullet_bank(path: str = DEFAULT_PATH) -> dict[str, str]:
    """{bullet_id: text} across experience + projects."""
    data = load(path)
    bullets = {}
    for role in data.get("experience", []):
        for b in role.get("bullets", []):
            bullets[b["id"]] = b["text"]
    for project in data.get("projects", []):
        for b in project.get("bullets", []):
            bullets[b["id"]] = b["text"]
    return bullets


def preferences(path: str = DEFAULT_PATH) -> dict:
    """The `preferences:` block (PROJECT.md §8): filter criteria a human
    edits, pipeline/filters.py reads (comp_floor, onsite_metros, ...).
    Unlike bullet_bank()/skill_years(), which have no sensible fallback and
    should fail loudly if resume.yaml is missing, filters.py has real
    defaults it can fall back to, so a missing file returns {} here instead
    of raising, rather than let a missing personal file break job
    classification entirely (e.g. on a from-scratch checkout with no
    resume.yaml yet, or in a test environment)."""
    try:
        data = load(path)
    except FileNotFoundError:
        return {}
    return data.get("preferences") or {}
