"""Regression tests for pipeline.tailor_bullets, pure, deterministic bullet-
count enforcement, no model calls. Split out of pipeline/tailor.py
2026-08-17 (DECISIONS.md #81) specifically so this could be tested without
a real API call; these are the tests that split was for.

Uses a small synthetic resume dict, not the real personal resume.yaml, so
this runs on a fresh checkout with no personal/ directory at all.
"""
from pipeline.tailor_bullets import _fit_section, enforce_bullet_counts


def _bullets(*ids):
    return [{"id": i, "text": i} for i in ids]


def _resume(n_roles=2, n_projects=2, bullets_per=5):
    experience = [
        {"company": f"Co{i}", "title": "Engineer", "start": "2020-01", "end": None,
         "bullets": _bullets(*[f"role{i}-{j}" for j in range(bullets_per)])}
        for i in range(n_roles)
    ]
    projects = [
        {"name": f"Proj{i}", "start": "2019", "end": "2019",
         "bullets": _bullets(*[f"proj{i}-{j}" for j in range(bullets_per)])}
        for i in range(n_projects)
    ]
    return {"experience": experience, "projects": projects}


def test_fit_section_trims_an_over_selected_section_to_max():
    section = {"bullets": _bullets("a", "b", "c", "d", "e")}
    chosen = _fit_section(section, min_n=1, max_n=2, selected={"a", "b", "c", "d", "e"})
    assert chosen == ["a", "b"]  # trims from the end, keeps resume.yaml order


def test_fit_section_tops_up_an_under_selected_section_to_min():
    section = {"bullets": _bullets("a", "b", "c", "d")}
    chosen = _fit_section(section, min_n=3, max_n=5, selected={"a"})
    assert chosen == ["a", "b", "c"]  # tops up with the next in order, not "d"


def test_fit_section_flagship_bullets_are_always_included_and_survive_the_trim():
    # Flagship bullets are first in resume.yaml order, so the max_n trim
    # (which drops from the end) can never cut them.
    section = {"bullets": _bullets("a", "b", "c", "d", "e")}
    chosen = _fit_section(section, min_n=1, max_n=2, selected={"e"}, flagship_n=1)
    assert chosen == ["a", "e"]  # flagship "a" included even though unselected


def test_enforce_bullet_counts_anchor_role_gets_four_to_six():
    resume = _resume(n_roles=1, n_projects=0)
    result = enforce_bullet_counts([], resume)  # model selected nothing
    assert 4 <= len(result) <= 6


def test_enforce_bullet_counts_second_role_gets_a_real_floor_not_zero():
    resume = _resume(n_roles=2, n_projects=0)
    # Model selected only from the anchor role, nothing from the second.
    anchor_ids = [b["id"] for b in resume["experience"][0]["bullets"][:4]]
    result = enforce_bullet_counts(anchor_ids, resume)
    second_role_bullets = [bid for bid in result if bid.startswith("role1-")]
    assert len(second_role_bullets) >= 2  # SECOND_ROLE_MIN_BULLETS


def test_enforce_bullet_counts_promotes_a_project_with_zero_selection_to_the_floor():
    resume = _resume(n_roles=1, n_projects=2)
    anchor_ids = [b["id"] for b in resume["experience"][0]["bullets"][:4]]
    result = enforce_bullet_counts(anchor_ids, resume)  # nothing from either project
    proj0 = [bid for bid in result if bid.startswith("proj0-")]
    proj1 = [bid for bid in result if bid.startswith("proj1-")]
    assert len(proj0) >= 2 and len(proj1) >= 2  # both promoted to PROJECT_TARGET_COUNT


def test_enforce_bullet_counts_does_not_promote_a_project_beyond_the_target_count():
    # 3 projects, target is 2: the model's own picks decide which 2, a
    # third with zero selection stays at 0, not forced up too.
    resume = _resume(n_roles=1, n_projects=3)
    anchor_ids = [b["id"] for b in resume["experience"][0]["bullets"][:4]]
    picked = [resume["projects"][0]["bullets"][0]["id"], resume["projects"][1]["bullets"][0]["id"]]
    result = enforce_bullet_counts(anchor_ids + picked, resume)
    proj2 = [bid for bid in result if bid.startswith("proj2-")]
    assert proj2 == []
