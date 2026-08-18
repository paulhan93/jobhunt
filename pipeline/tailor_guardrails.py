"""Deterministic guardrails against a tailored resume shipping something the
model wasn't supposed to produce (step 7, PROJECT.md §8). Split out of
pipeline/tailor.py 2026-08-17 (DECISIONS.md #81): this is the single most
safety-critical logic in the tailoring pipeline (it's what stands between a
real applicant and a fabricated resume claim), and it was previously buried
inline inside tailor_resume() where it could only be exercised by a real,
paid API call. Pure functions here, no model calls, directly testable.

Two failure modes found on real, live tailoring runs, both now guarded
here: unearned "hallucination"-language framing appended to a reworded
bullet (DECISIONS.md #64, #74, recurred more than once), and a fabricated
professional title in the generated summary (DECISIONS.md #76, "Data
Infrastructure Engineer", never actually held). Both lessons the same:
a prompted rule isn't a guaranteed one, so revert (don't just flag) any
violation deterministically.
"""
import re

_HALLUCINATION_RE = re.compile(r"hallucinat", re.I)

# Blocklist rather than an allowlist-shaped title parser: a fabricated title
# claim showed up in ordinary sentence case ("Data infrastructure engineer
# with..."), not Title Case, so a capitalization-based regex couldn't have
# found it reliably. Checking for the specific disallowed phrases actually
# plausible given this bullet bank's real content (infra/cloud/AI-adjacent
# work, but never held as a title) is more reliable than trying to parse
# "is this phrase a title claim" in general.
DISALLOWED_TITLES = [
    "data infrastructure engineer", "backend engineer", "frontend engineer",
    "full stack engineer", "full-stack engineer", "platform engineer",
    "devops engineer", "site reliability engineer", "infrastructure engineer",
    "cloud engineer", "systems engineer", "data engineer", "ai engineer",
    "machine learning engineer", "ml engineer", "solutions engineer",
    "product engineer", "systems architect", "solutions architect",
]


def validate_known_ids(
    selected_bullets: list[str], reword: dict[str, str], skills_order: list[str],
    bullet_ids: list[str], skill_ids: list[str],
) -> None:
    """Raises ValueError if the model referenced any id outside the known
    set. The JSON schema enum already keeps the model from inventing an ID,
    but this re-checks in Python rather than trust the provider's
    enforcement blindly, decision 43 found real cross-provider
    schema-enforcement gaps, and this is the one call site where an
    invented ID means a fabricated employment claim, not just a bad match."""
    unknown_bullets = set(selected_bullets) - set(bullet_ids)
    if unknown_bullets:
        raise ValueError(f"model selected unknown bullet ids: {unknown_bullets}")
    unknown_reword = set(reword) - set(bullet_ids)
    if unknown_reword:
        raise ValueError(f"model reworded unknown bullet ids: {unknown_reword}")
    unknown_skills = set(skills_order) - set(skill_ids)
    if unknown_skills:
        raise ValueError(f"model referenced unknown skill group ids: {unknown_skills}")


def revert_hallucination_language(reword: dict[str, str], bank: dict[str, str]) -> dict[str, str]:
    """Drops any reword entry that introduces "hallucinat*" language the
    original bullet didn't already have. "Hallucination" language has shown
    up unprompted in reworded text on real runs (2026-08-17, jobhunt-2 on a
    live Honeycomb tailor: "... computed entirely by deterministic
    arithmetic, eliminating hallucination risk", not in the original) even
    though the prompt already says not to append interpretive framing. A
    bullet whose ORIGINAL text already says "eliminate hallucinations"
    (privew-4) is fine to keep saying that in a reword; introducing the
    word into a bullet that never had it isn't. Returns a new dict, doesn't
    mutate the input."""
    result = dict(reword)
    for bid, reworded in reword.items():
        original = bank.get(bid, "")
        if _HALLUCINATION_RE.search(reworded) and not _HALLUCINATION_RE.search(original):
            del result[bid]
    return result


def revert_fabricated_title(summary: str, starting_summary: str) -> str:
    """Reverts the whole summary to the untouched, already-approved
    starting_summary if it names a disallowed professional title, rather
    than try to salvage/edit it, a fabricated identity claim isn't a
    phrasing problem to patch."""
    summary_lower = summary.lower()
    if any(title in summary_lower for title in DISALLOWED_TITLES):
        return starting_summary
    return summary
