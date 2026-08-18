"""Regression tests for pipeline.tailor_guardrails, the most safety-critical
logic in the tailoring pipeline (it's what stands between a real applicant
and a fabricated resume claim), previously only exercisable via a real, paid
API call since it was inline inside tailor_resume(). Split out 2026-08-17
(DECISIONS.md #81) specifically so this could be tested directly.
"""
import pytest

from pipeline.tailor_guardrails import (
    revert_fabricated_title,
    revert_hallucination_language,
    validate_known_ids,
)


def test_validate_known_ids_passes_with_all_valid_ids():
    validate_known_ids(["b1"], {"b1": "reworded"}, ["s1"], ["b1", "b2"], ["s1", "s2"])


def test_validate_known_ids_raises_on_unknown_bullet():
    with pytest.raises(ValueError, match="unknown bullet ids"):
        validate_known_ids(["nope"], {}, [], ["b1"], ["s1"])


def test_validate_known_ids_raises_on_unknown_reword_id():
    with pytest.raises(ValueError, match="unknown bullet ids"):
        validate_known_ids([], {"nope": "text"}, [], ["b1"], ["s1"])


def test_validate_known_ids_raises_on_unknown_skill_group():
    with pytest.raises(ValueError, match="unknown skill group ids"):
        validate_known_ids([], {}, ["nope"], ["b1"], ["s1"])


def test_revert_hallucination_language_drops_a_newly_introduced_hallucination_claim():
    # DECISIONS.md #74, the real jobhunt-2/Honeycomb case: the model added
    # "eliminating hallucination risk" to a bullet that never said it.
    bank = {"b1": "Built a deterministic arithmetic scoring pipeline."}
    reword = {"b1": "Built a scoring pipeline, eliminating hallucination risk."}
    result = revert_hallucination_language(reword, bank)
    assert "b1" not in result


def test_revert_hallucination_language_keeps_a_reword_when_the_original_already_says_it():
    # privew-4's real original text already says "eliminate hallucinations",
    # a reword of THAT bullet keeping the word is fine.
    bank = {"privew-4": "Designed a RAG pipeline to eliminate hallucinations in responses."}
    reword = {"privew-4": "Designed a RAG pipeline that eliminates hallucinations end to end."}
    result = revert_hallucination_language(reword, bank)
    assert "privew-4" in result


def test_revert_hallucination_language_leaves_unrelated_rewords_untouched():
    bank = {"b1": "Cut CI feedback time from 40 to 9 minutes."}
    reword = {"b1": "Reduced CI feedback time from 40 to 9 minutes across 60 engineers."}
    result = revert_hallucination_language(reword, bank)
    assert result == reword


def test_revert_hallucination_language_does_not_mutate_the_input():
    bank = {"b1": "Original text."}
    reword = {"b1": "Reworded text, eliminating hallucination risk."}
    revert_hallucination_language(reword, bank)
    assert "b1" in reword  # the caller's dict is untouched


def test_revert_fabricated_title_reverts_to_starting_summary():
    # DECISIONS.md #76, the real case: "Data Infrastructure Engineer",
    # never actually held.
    starting = "Software Engineer in Test focused on developer productivity."
    fabricated = "Data infrastructure engineer with a track record of building pipelines."
    assert revert_fabricated_title(fabricated, starting) == starting


def test_revert_fabricated_title_is_case_insensitive():
    # The real fabricated title showed up in sentence case, not Title Case,
    # this is exactly why the guard can't rely on capitalization.
    starting = "Software Engineer in Test."
    fabricated = "A BACKEND ENGINEER with deep systems experience."
    assert revert_fabricated_title(fabricated, starting) == starting


def test_revert_fabricated_title_leaves_a_clean_summary_untouched():
    starting = "Software Engineer in Test focused on developer productivity."
    clean = "Software Engineer focused on developer productivity, with experience in CI/CD."
    assert revert_fabricated_title(clean, starting) == clean
