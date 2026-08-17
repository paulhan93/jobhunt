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

import yaml
from pydantic import BaseModel, Field

from pipeline.extract import call_claude, load_bullet_bank

# Per-role/project bullet-count rules (Paul's call, 2026-08-16). The anchor
# role — the most recent experience entry, resume["experience"][0] — is the
# strongest, most current signal and must always appear, with 4-6 bullets.
# Not hardcoded to a company name: whichever job is first/most recent in
# resume.yaml is the anchor, so this keeps working the day that's no longer
# Oracle. Every other role/project caps at 4 (2-3 is the actual target,
# stated in the prompt); 0 is still fine for those — omitting an irrelevant
# role/project entirely is a real option the anchor role doesn't have.
ANCHOR_MIN_BULLETS = 4
ANCHOR_MAX_BULLETS = 6
OTHER_MAX_BULLETS = 4


class TailoredResume(BaseModel):
    summary: str
    selected_bullets: list[str]
    skills_order: list[str]
    reword: dict[str, str] = Field(default_factory=dict)


def load_resume(path: str = "resume.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def enforce_bullet_counts(selected_bullets: list[str], resume: dict) -> list[str]:
    """Deterministic backstop for ANCHOR_MIN/MAX_BULLETS and
    OTHER_MAX_BULLETS — the prompt asks the model for these targets, but a
    prompted count is a request, not a guarantee (same reasoning as the
    JSON-schema-enum-plus-Python-recheck pattern in tailor_resume() below).
    Runs per role/project, in each section's own resume.yaml bullet order:
    trims an over-selected section down to its max by dropping from the end
    of that order, and tops up an under-selected anchor role by adding the
    next not-yet-selected bullets in that same order. Order is the only
    signal available (selected_bullets doesn't carry a priority ranking),
    but it's a reasonable one — resume.yaml's bullets are already authored
    roughly most-to-least essential within each role."""
    experience = resume.get("experience", [])
    projects = resume.get("projects", [])
    selected = set(selected_bullets)

    sections = []
    if experience:
        sections.append((experience[0], ANCHOR_MIN_BULLETS, ANCHOR_MAX_BULLETS))
        sections += [(role, 0, OTHER_MAX_BULLETS) for role in experience[1:]]
    sections += [(project, 0, OTHER_MAX_BULLETS) for project in projects]

    result = []
    for section, min_n, max_n in sections:
        ids_in_order = [b["id"] for b in section.get("bullets", [])]
        chosen = [bid for bid in ids_in_order if bid in selected]
        if len(chosen) > max_n:
            chosen = chosen[:max_n]
        elif len(chosen) < min_n:
            remaining = [bid for bid in ids_in_order if bid not in chosen]
            chosen += remaining[: min_n - len(chosen)]
        result.extend(chosen)

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
what the bullet bank actually supports.
- "selected_bullets": bullet IDs (from the set above, exactly as given) that \
together make the strongest case for this specific job, following these \
per-role/project targets:
  * {anchor_company} ({anchor_title}), the most recent role — ALWAYS \
include this role. Select 4 to 6 bullets from it, never fewer than 4.
  * Every other role or project — 2 to 3 bullets is the target; 4 only if \
genuinely necessary to make the case for this specific job. 0 is fine if a \
role or project isn't relevant here at all.
  Within those targets, favor bullets that cover a requirement above, but a \
strong unmatched bullet is fine too.
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
    prompt = _TAILOR_PROMPT.format(
        title=job["title"],
        company=job["company"] or "Unknown",
        role_family=job["role_family"] or "swe",
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

    # The prompt states ANCHOR_MIN/MAX_BULLETS and OTHER_MAX_BULLETS as
    # targets, but a prompted count is a request, not a guarantee — enforce
    # it deterministically rather than trust the model hit it.
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
