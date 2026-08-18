"""Regression tests for pipeline.ats, pure functions, no network. These
absorb per-ATS response-shape differences (PROJECT.md §5's "quirks" list),
which is exactly the kind of thing that's cheap to lock in with a fixture
and easy to silently break while adding a sixth ATS someday.
"""
from pipeline.ats import count_jobs, slug_candidates


def test_slug_candidates_strips_a_trailing_corp_suffix():
    assert slug_candidates("Acme Inc") == ["acme", "acmehq"]


def test_slug_candidates_strips_punctuation_before_matching():
    # "Acme, Inc." and "Acme Inc" must resolve to the same candidates,
    # slug discovery only works if punctuation doesn't create false misses.
    assert slug_candidates("Acme, Inc.") == slug_candidates("Acme Inc")


def test_slug_candidates_expands_ampersand_to_and():
    assert "acmeand" in slug_candidates("Acme & Co")


def test_slug_candidates_returns_empty_when_the_whole_name_is_suffix_words():
    # "Systems" and "Inc" are both in _SUFFIXES, stripping both from the end
    # leaves nothing to build a slug from.
    assert slug_candidates("Systems Inc") == []


def test_slug_candidates_handles_a_leading_the():
    candidates = slug_candidates("The Very Group")
    assert "thevery" in candidates
    assert "very" in candidates  # "the" dropped as its own candidate too


def test_count_jobs_lever_is_a_bare_array():
    assert count_jobs("lever", [1, 2, 3]) == 3


def test_count_jobs_lever_non_list_is_zero_not_a_crash():
    assert count_jobs("lever", {}) == 0


def test_count_jobs_smartrecruiters_uses_content_key_not_jobs():
    # SmartRecruiters names its list "content", every other ATS uses "jobs".
    assert count_jobs("smartrecruiters", {"content": [1, 2, 3]}) == 3
    assert count_jobs("smartrecruiters", {"jobs": [1, 2, 3]}) == 0


def test_count_jobs_missing_or_wrong_type_key_is_zero_not_a_crash():
    # PROJECT.md §5: field types are not always what the name suggests,
    # count_jobs must not raise on a malformed/missing list.
    assert count_jobs("ashby", {"jobs": None}) == 0
    assert count_jobs("workable", "not a dict") == 0
