"""Deterministic bullet-count enforcement for tailored resumes (step 7,
PROJECT.md §8). Split out of pipeline/tailor.py 2026-08-17 (DECISIONS.md
#81): pure, no model calls, no I/O, so it can be tested directly instead of
only exercised indirectly through a real API call.

The model is asked for these per-role/project targets in the prompt
(pipeline/tailor_prompts.py), but a prompted count is a request, not a
guarantee, this module is the backstop that actually enforces it.
"""

# Per-role/project bullet-count rules (Paul's call, 2026-08-16; revised
# 2026-08-17 after the first real tailored PDF came out well under a page).
# The anchor role, the most recent experience entry, resume["experience"][0],
# is the strongest, most current signal and must always appear, with 4-6
# bullets. Not hardcoded to a company name: whichever job is first/most
# recent in resume.yaml is the anchor, so this keeps working the day that's
# no longer Oracle.
ANCHOR_MIN_BULLETS = 4
ANCHOR_MAX_BULLETS = 6

# Originally every non-anchor role/project could drop to 0 bullets if the
# model judged it irrelevant, in practice this let a real resume come out
# with only one role and one project, reading as sparse/incomplete rather
# than "correctly omitted irrelevant content". Paul's call: a second past
# role should almost always show *some* real content (it's real experience,
# not filler), so it gets a real floor instead of 0.
SECOND_ROLE_MIN_BULLETS = 2
SECOND_ROLE_MAX_BULLETS = 3

# Both projects show up by default now, not just for ai_eng, with only 2
# projects in the bank, "ai_eng gets both, everyone else gets 1" and "always
# both" are equivalent once the default target is 2, so the family-specific
# branching that used to live here was collapsed (2026-08-17, after
# confirming there was room on the page). PROJECT_MIN_BULLETS_PER_PROJECT is
# the floor for any project that IS included, so a "promoted" project
# doesn't show up as a single token bullet.
PROJECT_TARGET_COUNT = 2
PROJECT_MIN_BULLETS_PER_PROJECT = 2
PROJECT_MAX_BULLETS_PER_PROJECT = 3

# "Flagship" bullets (Paul's call, 2026-08-17): the model is told an
# unmatched-but-strong bullet is fine to include, but that's a soft nudge
# like every other prompted target here, nothing forced it to actually
# happen, so a section's single most impressive bullet could get dropped
# some runs just because it didn't obviously connect to this specific JD.
# resume.yaml's bullets are already ordered most-to-least essential per
# section (existing convention, see _fit_section), so "first N in that
# order" is a real signal, not an arbitrary pick, no new resume.yaml field
# needed. Anchor gets 2 (it's carrying the most weight), everything else
# gets 1.
ANCHOR_FLAGSHIP_COUNT = 2
OTHER_FLAGSHIP_COUNT = 1


def _fit_section(
    section: dict, min_n: int, max_n: int, selected: set[str], flagship_n: int = 0
) -> list[str]:
    """One role/project against its own min/max, in resume.yaml's own bullet
    order, trims an over-selected section from the end of that order, tops
    up an under-selected one with the next not-yet-selected bullets in that
    same order. Order is the only signal available beyond flagship_n
    (selected_bullets doesn't carry a priority ranking), but it's a
    reasonable one, resume.yaml's bullets are already authored roughly
    most-to-least essential per role.

    flagship_n forces the first flagship_n bullets (in that same order) into
    the result regardless of what the model picked. Since they're first in
    order, they're also first in `chosen` below, which means the max_n trim
    (which drops from the end) can never cut them, no separate protection
    needed."""
    ids_in_order = [b["id"] for b in section.get("bullets", [])]
    flagship = set(ids_in_order[:flagship_n])
    chosen = [bid for bid in ids_in_order if bid in selected or bid in flagship]
    if len(chosen) > max_n:
        chosen = chosen[:max_n]
    elif len(chosen) < min_n:
        remaining = [bid for bid in ids_in_order if bid not in chosen]
        chosen += remaining[: min_n - len(chosen)]
    return chosen


def enforce_bullet_counts(selected_bullets: list[str], resume: dict) -> list[str]:
    """Deterministic backstop for the bullet-count constants above, the
    prompt asks the model for these targets, but a prompted count is a
    request, not a guarantee (same reasoning as the JSON-schema-enum-plus-
    Python-recheck pattern in pipeline.tailor_guardrails)."""
    experience = resume.get("experience", [])
    projects = resume.get("projects", [])
    selected = set(selected_bullets)

    result = []

    if experience:
        result += _fit_section(
            experience[0], ANCHOR_MIN_BULLETS, ANCHOR_MAX_BULLETS, selected, ANCHOR_FLAGSHIP_COUNT
        )
        for role in experience[1:]:
            result += _fit_section(
                role, SECOND_ROLE_MIN_BULLETS, SECOND_ROLE_MAX_BULLETS, selected, OTHER_FLAGSHIP_COUNT
            )

    # Projects: let the model's own selection decide WHICH project(s) are
    # more relevant (never override that judgment), but guarantee at least
    # PROJECT_TARGET_COUNT of them end up with real content instead of
    # silently going to 0, promote in resume.yaml order, only if still
    # short of the target after the model's own picks.
    n_with_content = sum(
        1 for p in projects if any(b["id"] in selected for b in p.get("bullets", []))
    )
    for project in projects:
        has_any = any(b["id"] in selected for b in project.get("bullets", []))
        if not has_any and n_with_content < PROJECT_TARGET_COUNT:
            selected |= {b["id"] for b in project.get("bullets", [])[:PROJECT_MIN_BULLETS_PER_PROJECT]}
            n_with_content += 1
            has_any = True
        # Only a project that's actually included (model picked it, or it
        # was just promoted above) gets the real-content floor, one beyond
        # PROJECT_TARGET_COUNT with zero selection correctly stays at 0, not
        # forced up to the floor too.
        min_n = PROJECT_MIN_BULLETS_PER_PROJECT if has_any else 0
        flagship_n = OTHER_FLAGSHIP_COUNT if has_any else 0
        result += _fit_section(
            project, min_n, PROJECT_MAX_BULLETS_PER_PROJECT, selected, flagship_n
        )

    return result
