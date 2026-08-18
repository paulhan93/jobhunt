"""Regression tests for pipeline.tailor_prompts, pure string/schema
construction, no model calls. Split out of pipeline/tailor.py 2026-08-17
(DECISIONS.md #81).
"""
from pipeline.tailor_prompts import (
    build_prompt,
    build_schema,
    format_bank,
    format_requirements,
    format_skills,
    skill_group_ids,
)


def test_skill_group_ids_returns_ids_in_resume_order():
    resume = {"skills": [{"id": "skill-ci", "label": "CI/CD"}, {"id": "skill-ai", "label": "AI"}]}
    assert skill_group_ids(resume) == ["skill-ci", "skill-ai"]


def test_build_schema_constrains_bullets_and_skills_to_the_known_sets():
    schema = build_schema(["b1", "b2"], ["s1"])
    assert schema["properties"]["selected_bullets"]["items"]["enum"] == ["b1", "b2"]
    assert schema["properties"]["skills_order"]["items"]["enum"] == ["s1"]
    assert schema["properties"]["reword"]["items"]["properties"]["id"]["enum"] == ["b1", "b2"]
    assert schema["additionalProperties"] is False


def test_format_requirements_shows_the_already_matched_hint():
    reqs = [{"kind": "must", "text": "Python", "matched_bullets": '["b1", "b2"]'}]
    out = format_requirements(reqs)
    assert "[must] Python" in out
    assert "already-matched: b1, b2" in out


def test_format_requirements_shows_no_existing_match_when_empty():
    reqs = [{"kind": "nice", "text": "Kubernetes", "matched_bullets": None}]
    out = format_requirements(reqs)
    assert "no existing match" in out


def test_format_requirements_handles_no_requirements():
    assert format_requirements([]) == "(none extracted)"


def test_format_bank_includes_roles_and_projects_with_tags():
    resume = {
        "experience": [{"company": "Acme", "title": "Engineer", "start": "2020-01", "end": None,
                         "bullets": [{"id": "b1", "text": "Did a thing", "tags": ["python"]}]}],
        "projects": [{"name": "Widget", "start": "2019", "end": "2019",
                      "bullets": [{"id": "p1", "text": "Built it", "tags": []}]}],
    }
    out = format_bank(resume)
    assert "Acme | Engineer" in out
    assert "b1: Did a thing (tags: python)" in out
    assert "Widget (project)" in out
    assert "p1: Built it (tags: none)" in out


def test_format_skills_separates_label_from_items_unambiguously():
    resume = {"skills": [{"id": "skill-ci", "label": "CI/CD & Build", "items": ["GitHub Actions", "Jenkins"]}]}
    out = format_skills(resume)
    # The label must be visually distinguishable from the items list, not
    # just another comma-separated entry.
    assert out == "skill-ci: CI/CD & Build - GitHub Actions, Jenkins"


def test_build_prompt_includes_job_and_anchor_role_fields():
    resume = {
        "experience": [{"company": "Acme", "title": "Senior Engineer", "start": "2020-01", "end": None, "bullets": []}],
        "projects": [], "skills": [],
    }
    job = {"title": "Platform Engineer", "company": "TargetCo", "role_family": "platform"}
    prompt = build_prompt(job, [], resume, "Starting summary text.", "Full JD text here.")
    assert "Platform Engineer at TargetCo (platform track)" in prompt
    assert "Full JD text here." in prompt
    assert "Acme (Senior Engineer)" in prompt
    assert "Starting summary text." in prompt


def test_build_prompt_falls_back_to_not_available_with_no_jd_text():
    resume = {"experience": [{"company": "Acme", "title": "Engineer", "start": "2020-01", "end": None, "bullets": []}],
              "projects": [], "skills": []}
    job = {"title": "Engineer", "company": "TargetCo", "role_family": "swe"}
    prompt = build_prompt(job, [], resume, "Summary.", None)
    assert "(not available)" in prompt
