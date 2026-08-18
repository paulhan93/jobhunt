"""Resume tailoring (step 7, PROJECT.md §8). One Claude call per job, always,
this is the "cloud model for quality" half of §3's local-for-volume/cloud-for-
quality split; it doesn't go through extract.py's Ollama/Claude PROVIDER
switch because tailoring is never high-volume (a handful of resumes actually
sent, not thousands of postings).

The model selects and lightly rewords; it never invents. `selected_bullets`
and `reword` are both constrained by the JSON schema to the resume bank's own
IDs, so the model structurally cannot fabricate a bullet or regenerate
employment history from prose (§8), same mitigation shape as extract.py's
match_evidence().

Split 2026-08-17 (DECISIONS.md #81) into four modules, this file is now just
the orchestrator: pipeline.tailor_prompts (request shaping), pipeline.
tailor_bullets (deterministic bullet-count enforcement), and pipeline.
tailor_guardrails (the "don't trust the model" checks) each own one concern
and are directly testable without an API key; only this file's tailor_resume()
actually calls the model.
"""
from pydantic import BaseModel, Field

from pipeline import resume_bank, tailor_bullets, tailor_guardrails, tailor_prompts
from pipeline.extract import call_claude, load_bullet_bank, strip_html


class TailoredResume(BaseModel):
    summary: str
    selected_bullets: list[str]
    skills_order: list[str]
    reword: dict[str, str] = Field(default_factory=dict)


# Thin re-export over pipeline/resume_bank.py's single cached loader, kept
# under this name so scripts/tailor.py's existing `load_resume()` call
# doesn't need to change.
load_resume = resume_bank.load


def tailor_resume(job, requirements, resume: dict) -> TailoredResume:
    bank = load_bullet_bank()  # {id: text} across experience + projects
    bullet_ids = list(bank.keys())
    skill_ids = tailor_prompts.skill_group_ids(resume)

    starting_summary = next(
        (s["text"] for s in resume["summaries"] if s["for"] == job["role_family"]),
        resume["summaries"][0]["text"],
    )

    jd_text = strip_html(job["description"])
    prompt = tailor_prompts.build_prompt(job, requirements, resume, starting_summary, jd_text)
    schema = tailor_prompts.build_schema(bullet_ids, skill_ids)

    result = call_claude([{"role": "user", "content": prompt}], schema)
    result["reword"] = {item["id"]: item["text"] for item in result.get("reword", [])}
    tailored = TailoredResume.model_validate(result)

    tailor_guardrails.validate_known_ids(
        tailored.selected_bullets, tailored.reword, tailored.skills_order,
        bullet_ids, skill_ids,
    )
    tailored.reword = tailor_guardrails.revert_hallucination_language(tailored.reword, bank)
    tailored.summary = tailor_guardrails.revert_fabricated_title(tailored.summary, starting_summary)

    # The prompt states these targets, but a prompted count is a request,
    # not a guarantee, enforce it deterministically rather than trust the
    # model hit it.
    tailored.selected_bullets = tailor_bullets.enforce_bullet_counts(tailored.selected_bullets, resume)

    return tailored


def reword_diffs(tailored: TailoredResume, resume: dict) -> list[tuple[str, str, str]]:
    """[(bullet_id, original, reworded), ...] every reworded bullet must be
    diffed against the original before it ships (§8): the model can only
    reference an existing bullet, never write new employment history, but a
    human still needs to see exactly what changed before it goes out.

    Excludes no-op entries (new_text identical to the original, after
    whitespace normalization), observed live: the model sometimes includes
    a bullet in `reword` without actually changing it, which would otherwise
    show up as a pointless "diff" with identical before/after text."""
    bank = load_bullet_bank()
    return [
        (bid, bank[bid], new_text)
        for bid, new_text in tailored.reword.items()
        if bid in bank and new_text.split() != bank[bid].split()
    ]


# A summary earns its place when the resume doesn't explain itself on title
# alone: career pivots, gaps, senior-scope framing. `sdet` is the one
# family that's a direct continuation of the current SDET title, not a
# pivot; every other family reframes "SDET" into something else to some
# degree (that reframing is the whole point of PROJECT.md §2's positioning
# narrative), so they keep it. This is a deterministic inclusion rule, not
# a model decision, same "model generates, logic judges" split as the rest
# of this pipeline (§7's extraction/scoring split, §8's bullet selection).
_NO_SUMMARY_FAMILIES = {"sdet"}


def wants_summary(role_family: str | None) -> bool:
    return role_family not in _NO_SUMMARY_FAMILIES


def build_resume_doc(job, tailored: TailoredResume, resume: dict) -> dict:
    """Resolve the model's selection against the full bank into a flat,
    render-ready structure. Pure data assembly, no model involved past this
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
