"""Prompt and JSON-schema construction for resume tailoring (step 7,
PROJECT.md §8). Split out of pipeline/tailor.py 2026-08-17 (DECISIONS.md
#81): everything here shapes the request to the model, no model call
itself, no judgment about the result. Pure string/dict building, directly
testable without an API key.
"""
import json


def skill_group_ids(resume: dict) -> list[str]:
    return [s["id"] for s in resume.get("skills", [])]


def build_schema(bullet_ids: list[str], skill_ids: list[str]) -> dict:
    # "reword" is an array of {id, text}, not an object with one optional
    # property per bullet id, Claude's structured-output rejects schemas
    # with more than 24 optional properties ("Schemas contains too many
    # optional parameters ... limit: 24"), and the resume bank already has
    # 29 bullet ids. Found by actually calling the API, not assumed, the
    # same array-of-objects shape extract.py's _build_matching_schema
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


def format_requirements(requirements) -> str:
    lines = []
    for r in requirements:
        matched = json.loads(r["matched_bullets"]) if r["matched_bullets"] else []
        hint = f" (already-matched: {', '.join(matched)})" if matched else " (no existing match)"
        lines.append(f"- [{r['kind']}] {r['text']}{hint}")
    return "\n".join(lines) if lines else "(none extracted)"


def format_bank(resume: dict) -> str:
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


def format_skills(resume: dict) -> str:
    return "\n".join(
        f"{s['id']}: {s['label']} - {', '.join(s['items'])}"
        for s in resume.get("skills", [])
    )


TAILOR_PROMPT = """You are tailoring a resume for one specific job \
application. The candidate's full bullet bank is deliberately larger than \
any single resume needs - select a strong subset, don't include everything.

Job: {title} at {company} ({role_family} track)

Full job posting, for context only: tone, what the role actually spends \
its time on, what's emphasized vs mentioned in passing. This is background \
for judgment, NOT a new source of claims: every fact in your output must \
still trace to a specific bullet in the candidate's bank below. Ignore \
boilerplate (benefits, EEO, background-check, perks, company mission) and \
focus on the actual role description:
{jd_block}

Extracted requirements for this job (already-matched bullet IDs are a hint \
from an earlier pass, not a restriction; use your own judgment):
{requirements_block}

Candidate's full bullet bank, grouped by role/project:
{bank_block}

Starting-point summary for this track (adapt it to this specific job, don't \
just copy it verbatim, and don't invent claims the bullet bank doesn't support):
{starting_summary}

Skill groups available (id: label - items):
{skills_block}

Return:
- "summary": 2-3 sentences tailored to this specific job, grounded only in \
what the bullet bank actually supports. Never claim a professional title or \
discipline the candidate hasn't actually held (their real title is Software \
Engineer in Test / SDET, nothing else): "Data Infrastructure Engineer," \
"Backend Engineer," "Platform Engineer," "DevOps Engineer," "AI Engineer," \
and similar are NOT acceptable even if some of their work touches that \
domain. Stick to "Software Engineer" (optionally "in Test" / "(SDET)"), \
"Test Automation Engineer," or "Automation Engineer" as the self-description, \
and use "focused on X" / "with experience in Y" phrasing for domain \
emphasis instead of inventing a new title.
- "selected_bullets": bullet IDs (from the set above, exactly as given) that \
together make the strongest case for this specific job, following these \
per-role/project targets:
  * {anchor_company} ({anchor_title}), the most recent role: ALWAYS \
include this role. Select 4 to 6 bullets from it, never fewer than 4.
  * Every other past role: include it too, 2 to 3 bullets. This is real \
experience, not filler; only drop it to 0 bullets if it's genuinely \
irrelevant to this specific job, which should be rare.
  * Projects: include both. 2 to 3 bullets from each.
  Within those targets, favor bullets that cover a requirement above, but a \
strong unmatched bullet is fine too. A resume that reads as sparse (too few \
bullets, empty space on the page) is a real failure mode here, not just a \
style preference; favor including a solid, relevant bullet over omitting it.
- "skills_order": skill group IDs (from the set above), most relevant to \
this job first. Omit a group only if it's genuinely irrelevant to this job.
- "reword": optional, at most 2-3 of your selected bullet IDs. A tightened \
version of that EXACT bullet's text: same facts, numbers, technologies, and \
scope as the original, just more concise or better-ordered. Do not append \
interpretive framing the original doesn't state (e.g. "-- demonstrating \
systems thinking", "-- showing strong ownership"); if you're not removing or \
reordering words from the original, it isn't a tightening. Leave bullets you \
don't want to reword out of this dict entirely; most bullets shouldn't need \
rewording at all."""


def build_prompt(
    job, requirements, resume: dict, starting_summary: str, jd_text: str | None
) -> str:
    anchor_role = resume["experience"][0]
    return TAILOR_PROMPT.format(
        title=job["title"],
        company=job["company"] or "Unknown",
        role_family=job["role_family"] or "swe",
        jd_block=jd_text or "(not available)",
        requirements_block=format_requirements(requirements),
        bank_block=format_bank(resume),
        starting_summary=starting_summary,
        skills_block=format_skills(resume),
        anchor_company=anchor_role["company"],
        anchor_title=anchor_role["title"],
    )
