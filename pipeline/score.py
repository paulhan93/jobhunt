"""Pure arithmetic fit scoring (step 6c, PROJECT.md §7c). No model calls here —
judgment is arithmetic over what extract.py already pulled out."""
import json
from datetime import date

import yaml

APPLY_THRESHOLD = 70
STRETCH_THRESHOLD = 40


def _years_between(start: str, end: str | None) -> float:
    sy, sm = (int(p) for p in start.split("-"))
    if end:
        ey, em = (int(p) for p in end.split("-"))
    else:
        today = date.today()
        ey, em = today.year, today.month
    return (ey - sy) + (em - sm) / 12


def load_skill_years(path: str = "resume.yaml") -> dict[str, float]:
    """Sum years per skill_key across experience roles (not projects — years
    of experience means employment) where at least one bullet carries the tag."""
    with open(path) as f:
        data = yaml.safe_load(f)

    years: dict[str, float] = {}
    for role in data.get("experience", []):
        role_years = _years_between(role["start"], role.get("end"))
        tags = {t for b in role.get("bullets", []) for t in b.get("tags", [])}
        for tag in tags:
            years[tag] = years.get(tag, 0.0) + role_years
    return years


def load_total_years(path: str = "resume.yaml") -> float:
    """Total professional (experience-only) years, for years-checks whose
    requirement has no skill_key — e.g. "5+ years of software engineering
    experience" isn't tied to any one skill in the controlled vocab, so it
    can't be looked up in load_skill_years() at all. Without this fallback,
    those requirements silently skipped the years-cap check entirely."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return sum(
        _years_between(role["start"], role.get("end"))
        for role in data.get("experience", [])
    )


def _matched(row) -> bool:
    raw = row["matched_bullets"]
    if not raw:
        return False
    return len(json.loads(raw)) > 0


def score_job(
    requirement_rows, skill_years: dict[str, float], total_years: float
) -> tuple[float, str] | tuple[None, None]:
    """Returns (fit_score, fit_tier), or (None, None) if there's nothing to
    score. Zero extracted requirements means extraction likely failed (bad
    JD, model hiccup, failed batch call) — it must never be treated as a
    perfect match, which is what the old must_hit/nice_hit `else 1.0`
    fallback did when requirement_rows was empty entirely."""
    if not requirement_rows:
        return None, None

    musts = [r for r in requirement_rows if r["kind"] == "must"]
    nices = [r for r in requirement_rows if r["kind"] == "nice"]

    must_hit = sum(_matched(r) for r in musts) / len(musts) if musts else 1.0
    nice_hit = sum(_matched(r) for r in nices) / len(nices) if nices else 1.0

    fit = 100 * (0.75 * must_hit + 0.25 * nice_hit)

    if fit >= APPLY_THRESHOLD:
        tier = "apply"
    elif fit >= STRETCH_THRESHOLD:
        tier = "stretch"
    else:
        tier = "skip"

    # Years cap: any must-have failing by more than ~1.5x caps at stretch,
    # regardless of score (PROJECT.md §7c).
    for r in musts:
        if r["years_required"] is None:
            continue
        have = skill_years.get(r["skill_key"], 0.0) if r["skill_key"] else total_years
        if have < r["years_required"] / 1.5 and tier == "apply":
            tier = "stretch"

    return round(fit, 1), tier
