"""Pure arithmetic fit scoring (step 6c, PROJECT.md §7c). No model calls here —
judgment is arithmetic over what extract.py already pulled out."""
import json
from datetime import date

import yaml

# Tier is decided directly off must_hit/nice_hit (fractions, 0-1), not off
# the blended fit_score - a single blended number conflates "how good are
# the musts" with "how good are the nices" in a way that doesn't map cleanly
# onto conditions statable in plain language. fit_score itself is unaffected
# and still drives the review-queue sort order.
#
# APPLY_MUST_THRESHOLD: must_hit at or above this is a clear "prioritize
# this" case on its own, no nice-to-have credit needed. Calibrated by
# reading real jobs at this line (2026-08-17), not picked blind.
APPLY_MUST_THRESHOLD = 0.70
# Grace zone: a job just under APPLY_MUST_THRESHOLD still qualifies for
# apply if nice-to-have evidence is genuinely strong. APPLY_GRACE_MIN_NICE
# guards against a single lucky nice-to-have producing a mathematically
# "perfect" nice_hit off a sample size of one - confirmed necessary by
# testing: without it, both the Honeycomb PM job (DECISIONS.md - already
# known inflated, see 2026-08-17 chat history) and a Sales Engineer role
# got promoted back into apply through exactly that loophole.
APPLY_GRACE_MUST_THRESHOLD = 0.55
APPLY_GRACE_NICE_THRESHOLD = 0.60
APPLY_GRACE_MIN_NICE = 3
STRETCH_MUST_THRESHOLD = 0.40


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


# How much a requirement's match counts toward must_hit/nice_hit. Replaces
# the old binary matched/unmatched: match_evidence() (PROJECT.md §7b, added
# migration 007) now rates how well the BEST matched bullet actually
# supports the requirement, not just whether one was found at all - a
# generic requirement stretched to fit a tangentially-related bullet
# ("weak") shouldn't count the same as a direct hit ("strong"). Weights are
# a starting guess, same status as APPLY_MUST_THRESHOLD/STRETCH_MUST_THRESHOLD above -
# tunable, not derived from outcome data yet.
_STRENGTH_WEIGHT = {"strong": 1.0, "moderate": 0.6, "weak": 0.25, "none": 0.0}


def _weight(row) -> float:
    strength = row["match_strength"]
    if strength is not None:
        return _STRENGTH_WEIGHT.get(strength, 0.0)
    # Rows matched before migration 007 (not yet backfilled by
    # scripts/rematch_all.py) have no strength rating - fall back to the old
    # binary behavior rather than silently scoring them as zero.
    return 1.0 if _matched(row) else 0.0


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

    must_hit = sum(_weight(r) for r in musts) / len(musts) if musts else 1.0

    # A job with zero extracted nice-to-haves has nothing to score in that
    # bucket - the old `else 1.0` fallback awarded a free 25% of the score
    # for a category that was never evaluated, not "no data" (confirmed
    # against real jobs: JDs that state every qualification as a flat
    # "required" list with no separate preferred/bonus section, not a
    # scraping or extraction gap). Score off must_hit alone instead of
    # assuming a perfect nice_hit.
    nice_hit = sum(_weight(r) for r in nices) / len(nices) if nices else None
    if nice_hit is None:
        fit = 100 * must_hit
    else:
        fit = 100 * (0.75 * must_hit + 0.25 * nice_hit)

    grace = (
        len(nices) >= APPLY_GRACE_MIN_NICE
        and must_hit >= APPLY_GRACE_MUST_THRESHOLD
        and (nice_hit or 0) >= APPLY_GRACE_NICE_THRESHOLD
    )
    if must_hit >= APPLY_MUST_THRESHOLD or grace:
        tier = "apply"
    elif must_hit >= STRETCH_MUST_THRESHOLD:
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
