"""Resume tailoring (step 7, PROJECT.md §8). One Claude call per job, always —
this is the "cloud model for quality" half of §3's local-for-volume/cloud-for-
quality split; it doesn't go through extract.py's Ollama/Claude PROVIDER
switch because tailoring is never high-volume (a handful of resumes actually
sent, not thousands of postings).

The model selects and lightly rewords; it never invents. `selected_bullets`
and `reword` are both constrained by the JSON schema to the resume bank's own
IDs, so the model structurally cannot fabricate a bullet or regenerate
employment history from prose (§8) — same mitigation shape as extract.py's
match_evidence().
"""
import json
import re

import yaml
from pydantic import BaseModel, Field

from pipeline.extract import call_claude, load_bullet_bank, strip_html

_HALLUCINATION_RE = re.compile(r"hallucinat", re.I)

# Guards a real, live failure mode: the model has claimed a professional
# title/discipline that was never actually held (2026-08-17, "Data
# Infrastructure Engineer" on a real tailored resume) - not a phrasing
# nitpick, a fabricated identity claim. The prompt now says not to, but a
# prompted rule isn't a guaranteed one, same lesson as everywhere else in
# this file. Blocklist rather than an allowlist-shaped title parser: title
# claims show up in ordinary sentence case ("Data infrastructure engineer
# with...") not Title Case, so a capitalization-based regex can't reliably
# find them - checking for the specific disallowed phrases actually
# plausible given this bullet bank's real content (infra/cloud/AI-adjacent
# work, but never held as a title) is more reliable than trying to parse
# "is this phrase a title claim" in general.
_DISALLOWED_TITLES = [
    "data infrastructure engineer", "backend engineer", "frontend engineer",
    "full stack engineer", "full-stack engineer", "platform engineer",
    "devops engineer", "site reliability engineer", "infrastructure engineer",
    "cloud engineer", "systems engineer", "data engineer", "ai engineer",
    "machine learning engineer", "ml engineer", "solutions engineer",
    "product engineer", "systems architect", "solutions architect",
]

# Per-role/project bullet-count rules (Paul's call, 2026-08-16; revised
# 2026-08-17 after the first real tailored PDF came out well under a page).
# The anchor role — the most recent experience entry, resume["experience"][0]
# — is the strongest, most current signal and must always appear, with 4-6
# bullets. Not hardcoded to a company name: whichever job is first/most
# recent in resume.yaml is the anchor, so this keeps working the day that's
# no longer Oracle.
ANCHOR_MIN_BULLETS = 4
ANCHOR_MAX_BULLETS = 6

# Originally every non-anchor role/project could drop to 0 bullets if the
# model judged it irrelevant - in practice this let a real resume come out
# with only one role and one project, reading as sparse/incomplete rather
# than "correctly omitted irrelevant content". Paul's call: a second past
# role should almost always show *some* real content (it's real experience,
# not filler), so it gets a real floor instead of 0.
SECOND_ROLE_MIN_BULLETS = 2
SECOND_ROLE_MAX_BULLETS = 3

# Both projects show up by default now, not just for ai_eng - with only 2
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
# like every other prompted target here - nothing forced it to actually
# happen, so a section's single most impressive bullet could get dropped
# some runs just because it didn't obviously connect to this specific JD.
# resume.yaml's bullets are already ordered most-to-least essential per
# section (existing convention, see _fit_section), so "first N in that
# order" is a real signal, not an arbitrary pick - no new resume.yaml field
# needed. Anchor gets 2 (it's carrying the most weight), everything else
# gets 1.
ANCHOR_FLAGSHIP_COUNT = 2
OTHER_FLAGSHIP_COUNT = 1


class TailoredResume(BaseModel):
    summary: str
    selected_bullets: list[str]
    skills_order: list[str]
    reword: dict[str, str] = Field(default_factory=dict)


def load_resume(path: str = "resume.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _fit_section(
    section: dict, min_n: int, max_n: int, selected: set[str], flagship_n: int = 0
) -> list[str]:
    """One role/project against its own min/max, in resume.yaml's own bullet
    order — trims an over-selected section from the end of that order, tops
    up an under-selected one with the next not-yet-selected bullets in that
    same order. Order is the only signal available beyond flagship_n
    (selected_bullets doesn't carry a priority ranking), but it's a
    reasonable one — resume.yaml's bullets are already authored roughly
    most-to-least essential per role.

    flagship_n forces the first flagship_n bullets (in that same order) into
    the result regardless of what the model picked. Since they're first in
    order, they're also first in `chosen` below, which means the max_n trim
    (which drops from the end) can never cut them - no separate protection
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
    """Deterministic backstop for the bullet-count constants above — the
    prompt asks the model for these targets, but a prompted count is a
    request, not a guarantee (same reasoning as the
    JSON-schema-enum-plus-Python-recheck pattern in tailor_resume() below)."""
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
    # silently going to 0 - promote in resume.yaml order, only if still
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
        # was just promoted above) gets the real-content floor - one beyond
        # PROJECT_TARGET_COUNT with zero selection correctly stays at 0, not
        # forced up to the floor too.
        min_n = PROJECT_MIN_BULLETS_PER_PROJECT if has_any else 0
        flagship_n = OTHER_FLAGSHIP_COUNT if has_any else 0
        result += _fit_section(
            project, min_n, PROJECT_MAX_BULLETS_PER_PROJECT, selected, flagship_n
        )

    return result


def skill_group_ids(resume: dict) -> list[str]:
    return [s["id"] for s in resume.get("skills", [])]


def _build_schema(bullet_ids: list[str], skill_ids: list[str]) -> dict:
    # "reword" is an array of {id, text}, not an object with one optional
    # property per bullet id — Claude's structured-output rejects schemas
    # with more than 24 optional properties ("Schemas contains too many
    # optional parameters ... limit: 24"), and the resume bank already has
    # 29 bullet ids. Found by actually calling the API, not assumed —
    # the same array-of-objects shape extract.py's _build_matching_schema
    # already uses for a similar id-keyed mapping. Converted back to a
    # {id: text} dict in tailor_resume() before validation, so the rest of
    # this module still works with TailoredResume.reword as a plain dict.
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "selected_bullets": {
                "type": "array",
                "items": {"type": "string", "enum": bullet_ids},
            },
            "skills_order": {
                "type": "array",
                "items": {"type": "string", "enum": skill_ids},
            },
            "reword": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "enum": bullet_ids},
                        "text": {"type": "string"},
                    },
                    "required": ["id", "text"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "selected_bullets", "skills_order", "reword"],
        "additionalProperties": False,
    }


def _format_requirements(requirements) -> str:
    lines = []
    for r in requirements:
        matched = json.loads(r["matched_bullets"]) if r["matched_bullets"] else []
        hint = f" (already-matched: {', '.join(matched)})" if matched else " (no existing match)"
        lines.append(f"- [{r['kind']}] {r['text']}{hint}")
    return "\n".join(lines) if lines else "(none extracted)"


def _format_bank(resume: dict) -> str:
    lines = []
    for role in resume.get("experience", []):
        dates = f"{role['start']} to {role.get('end') or 'present'}"
        lines.append(f"\n{role['company']} | {role['title']} | {dates}")
        for b in role.get("bullets", []):
            tags = ", ".join(b.get("tags", [])) or "none"
            lines.append(f"  {b['id']}: {b['text']} (tags: {tags})")
    for project in resume.get("projects", []):
        dates = f"{project['start']} to {project.get('end') or 'present'}"
        lines.append(f"\n{project['name']} (project) | {dates}")
        for b in project.get("bullets", []):
            tags = ", ".join(b.get("tags", [])) or "none"
            lines.append(f"  {b['id']}: {b['text']} (tags: {tags})")
    return "\n".join(lines)


_TAILOR_PROMPT = """You are tailoring a resume for one specific job \
application. The candidate's full bullet bank is deliberately larger than \
any single resume needs — select a strong subset, don't include everything.

Job: {title} at {company} ({role_family} track)

Full job posting, for context only — tone, what the role actually spends \
its time on, what's emphasized vs mentioned in passing. This is background \
for judgment, NOT a new source of claims: every fact in your output must \
still trace to a specific bullet in the candidate's bank below. Ignore \
boilerplate (benefits, EEO, background-check, perks, company mission) and \
focus on the actual role description:
{jd_block}

Extracted requirements for this job (already-matched bullet IDs are a hint \
from an earlier pass, not a restriction — use your own judgment):
{requirements_block}

Candidate's full bullet bank, grouped by role/project:
{bank_block}

Starting-point summary for this track (adapt it to this specific job — don't \
just copy it verbatim, and don't invent claims the bullet bank doesn't support):
{starting_summary}

Skill groups available (id: label — items):
{skills_block}

Return:
- "summary": 2-3 sentences tailored to this specific job, grounded only in \
what the bullet bank actually supports. Never claim a professional title or \
discipline the candidate hasn't actually held (their real title is Software \
Engineer in Test / SDET, nothing else) — "Data Infrastructure Engineer," \
"Backend Engineer," "Platform Engineer," "DevOps Engineer," "AI Engineer," \
and similar are NOT acceptable even if some of their work touches that \
domain. Stick to "Software Engineer" (optionally "in Test" / "(SDET)"), \
"Test Automation Engineer," or "Automation Engineer" as the self-description, \
and use "focused on X" / "with experience in Y" phrasing for domain \
emphasis instead of inventing a new title.
- "selected_bullets": bullet IDs (from the set above, exactly as given) that \
together make the strongest case for this specific job, following these \
per-role/project targets:
  * {anchor_company} ({anchor_title}), the most recent role — ALWAYS \
include this role. Select 4 to 6 bullets from it, never fewer than 4.
  * Every other past role — include it too, 2 to 3 bullets. This is real \
experience, not filler; only drop it to 0 bullets if it's genuinely \
irrelevant to this specific job, which should be rare.
  * Projects — include both. 2 to 3 bullets from each.
  Within those targets, favor bullets that cover a requirement above, but a \
strong unmatched bullet is fine too. A resume that reads as sparse (too few \
bullets, empty space on the page) is a real failure mode here, not just a \
style preference — favor including a solid, relevant bullet over omitting it.
- "skills_order": skill group IDs (from the set above), most relevant to \
this job first. Omit a group only if it's genuinely irrelevant to this job.
- "reword": optional, at most 2-3 of your selected bullet IDs. A tightened \
version of that EXACT bullet's text — same facts, numbers, technologies, and \
scope as the original, just more concise or better-ordered. Do not append \
interpretive framing the original doesn't state (e.g. "-- demonstrating \
systems thinking", "-- showing strong ownership"); if you're not removing or \
reordering words from the original, it isn't a tightening. Leave bullets you \
don't want to reword out of this dict entirely — most bullets shouldn't need \
rewording at all."""


def tailor_resume(job, requirements, resume: dict) -> TailoredResume:
    bank = load_bullet_bank()  # {id: text} across experience + projects
    bullet_ids = list(bank.keys())
    skill_ids = skill_group_ids(resume)

    starting_summary = next(
        (s["text"] for s in resume["summaries"] if s["for"] == job["role_family"]),
        resume["summaries"][0]["text"],
    )
    skills_block = "\n".join(
        f"{s['id']}: {s['label']} — {', '.join(s['items'])}"
        for s in resume.get("skills", [])
    )

    anchor_role = resume["experience"][0]
    jd_text = strip_html(job["description"] or "")
    prompt = _TAILOR_PROMPT.format(
        title=job["title"],
        company=job["company"] or "Unknown",
        role_family=job["role_family"] or "swe",
        jd_block=jd_text or "(not available)",
        requirements_block=_format_requirements(requirements),
        bank_block=_format_bank(resume),
        starting_summary=starting_summary,
        skills_block=skills_block,
        anchor_company=anchor_role["company"],
        anchor_title=anchor_role["title"],
    )

    schema = _build_schema(bullet_ids, skill_ids)
    result = call_claude([{"role": "user", "content": prompt}], schema)
    result["reword"] = {item["id"]: item["text"] for item in result.get("reword", [])}
    tailored = TailoredResume.model_validate(result)

    # The JSON schema enum already keeps the model from inventing an ID, but
    # re-check in Python rather than trust the provider's enforcement blindly
    # — decision 43 found real cross-provider schema-enforcement gaps, and
    # this is the one call site where an invented ID means a fabricated
    # employment claim, not just a bad match.
    unknown_bullets = set(tailored.selected_bullets) - set(bullet_ids)
    if unknown_bullets:
        raise ValueError(f"model selected unknown bullet ids: {unknown_bullets}")
    unknown_reword = set(tailored.reword) - set(bullet_ids)
    if unknown_reword:
        raise ValueError(f"model reworded unknown bullet ids: {unknown_reword}")
    unknown_skills = set(tailored.skills_order) - set(skill_ids)
    if unknown_skills:
        raise ValueError(f"model referenced unknown skill group ids: {unknown_skills}")

    # "Hallucination" language has shown up unprompted in reworded text on
    # real runs (2026-08-17, jobhunt-2 on a live Honeycomb tailor - "...
    # computed entirely by deterministic arithmetic, eliminating
    # hallucination risk" - not in the original) even though the prompt
    # already says not to append interpretive framing. Same lesson as
    # everywhere else here: a prompted rule isn't a guaranteed one, so
    # revert (not just flag) any reword that introduces the word when the
    # original bullet didn't already use it. privew-4's original text does
    # say "eliminate hallucinations" - a reword of THAT bullet keeping the
    # word is fine; introducing it into a bullet that never had it isn't.
    for bid in list(tailored.reword.keys()):
        original = bank.get(bid, "")
        reworded = tailored.reword[bid]
        if _HALLUCINATION_RE.search(reworded) and not _HALLUCINATION_RE.search(original):
            del tailored.reword[bid]

    # Same reasoning, for a more serious failure: the model claiming a
    # professional title never actually held (2026-08-17, "Data
    # Infrastructure Engineer"). Revert the whole summary to the untouched,
    # already-approved starting_summary rather than try to salvage/edit it -
    # a fabricated identity claim isn't a phrasing problem to patch.
    summary_lower = tailored.summary.lower()
    if any(title in summary_lower for title in _DISALLOWED_TITLES):
        tailored.summary = starting_summary

    # The prompt states these targets, but a prompted count is a request,
    # not a guarantee — enforce it deterministically rather than trust the
    # model hit it.
    tailored.selected_bullets = enforce_bullet_counts(tailored.selected_bullets, resume)

    return tailored


def reword_diffs(tailored: TailoredResume, resume: dict) -> list[tuple[str, str, str]]:
    """[(bullet_id, original, reworded), ...] — every reworded bullet must be
    diffed against the original before it ships (§8): the model can only
    reference an existing bullet, never write new employment history, but a
    human still needs to see exactly what changed before it goes out.

    Excludes no-op entries (new_text identical to the original, after
    whitespace normalization) — observed live: the model sometimes includes
    a bullet in `reword` without actually changing it, which would otherwise
    show up as a pointless "diff" with identical before/after text."""
    bank = load_bullet_bank()
    return [
        (bid, bank[bid], new_text)
        for bid, new_text in tailored.reword.items()
        if bid in bank and new_text.split() != bank[bid].split()
    ]


# A summary earns its place when the resume doesn't explain itself on title
# alone — career pivots, gaps, senior-scope framing. `sdet` is the one
# family that's a direct continuation of the current SDET title, not a
# pivot; every other family reframes "SDET" into something else to some
# degree (that reframing is the whole point of PROJECT.md §2's positioning
# narrative), so they keep it. This is a deterministic inclusion rule, not
# a model decision — same "model generates, logic judges" split as the rest
# of this pipeline (§7's extraction/scoring split, §8's bullet selection).
_NO_SUMMARY_FAMILIES = {"sdet"}


def wants_summary(role_family: str | None) -> bool:
    return role_family not in _NO_SUMMARY_FAMILIES


def build_resume_doc(job, tailored: TailoredResume, resume: dict) -> dict:
    """Resolve the model's selection against the full bank into a flat,
    render-ready structure. Pure data assembly — no model involved past this
    point, and bullet order within a role/project is never reordered by the
    model, only filtered and (optionally) reworded."""
    selected = set(tailored.selected_bullets)

    def resolve(bullets):
        return [
            {"id": b["id"], "text": tailored.reword.get(b["id"], b["text"])}
            for b in bullets
            if b["id"] in selected
        ]

    experience = []
    for role in resume.get("experience", []):
        bullets = resolve(role.get("bullets", []))
        if bullets:
            experience.append({**role, "bullets": bullets})

    projects = []
    for project in resume.get("projects", []):
        bullets = resolve(project.get("bullets", []))
        if bullets:
            projects.append({**project, "bullets": bullets})

    skills_by_id = {s["id"]: s for s in resume.get("skills", [])}
    skills = [skills_by_id[i] for i in tailored.skills_order if i in skills_by_id]

    return {
        "contact": resume["contact"],
        "summary": tailored.summary if wants_summary(job["role_family"]) else None,
        "experience": experience,
        "projects": projects,
        "skills": skills,
        "education": resume.get("education", []),
    }
