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


def _matched(row) -> bool:
    raw = row["matched_bullets"]
    if not raw:
        return False
    return len(json.loads(raw)) > 0


def score_job(requirement_rows, skill_years: dict[str, float]) -> tuple[float, str]:
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

    # Years cap: any must-have failing by more than ~2x caps at stretch,
    # regardless of score (PROJECT.md §7c).
    for r in musts:
        if r["years_required"] is None or not r["skill_key"]:
            continue
        have = skill_years.get(r["skill_key"], 0.0)
        if have < r["years_required"] / 2 and tier == "apply":
            tier = "stretch"

    return round(fit, 1), tier
