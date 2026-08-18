"""Regression tests for pipeline.score.score_job(), pure function, no DB, no
model. The two most important cases here mirror each other on purpose: a
symmetric bug (vacuous full credit for an empty must/nice bucket) was found
and fixed twice, on the nice side first, then the must side eight days later
(DECISIONS.md), so both halves get a test rather than just the one that
actually broke.
"""
from pipeline.score import score_job


def req(kind, strength, skill_key=None, years_required=None):
    return {"kind": kind, "skill_key": skill_key, "years_required": years_required,
            "match_strength": strength}


def test_zero_musts_scores_off_nice_hit_only_not_a_free_pass():
    # A JD with everything under "Preferred", nothing marked required.
    reqs = [req("nice", "weak")]
    fit, tier = score_job(reqs, {}, total_years=5.0)
    assert fit < 30
    assert tier == "skip"


def test_zero_nices_scores_off_must_hit_only_not_a_free_pass():
    # The earlier version of this same bug: a job with no nice-to-haves
    # extracted must not get a free 25% of fit_score for a category that was
    # never evaluated.
    reqs = [req("must", "weak")]
    fit, tier = score_job(reqs, {}, total_years=5.0)
    assert fit < 30
    assert tier == "skip"


def test_all_strong_musts_and_no_nices_scores_off_must_hit_alone():
    reqs = [req("must", "strong"), req("must", "strong")]
    fit, tier = score_job(reqs, {}, total_years=5.0)
    assert fit == 100.0
    assert tier == "apply"


def test_grace_zone_needs_at_least_three_nice_matches():
    # A single "strong" nice-to-have must not be enough to promote a
    # borderline must_hit into apply, the loophole that let a real PM job
    # and a real Sales Engineer role both get mis-promoted before the
    # APPLY_GRACE_MIN_NICE guard existed.
    reqs = [req("must", "moderate")] + [req("nice", "strong")]
    _, tier = score_job(reqs, {}, total_years=5.0)
    assert tier != "apply"


def test_grace_zone_promotes_with_three_or_more_strong_nice_matches():
    # The case the grace zone exists to rescue: borderline must coverage,
    # genuinely strong nice-to-have evidence, sample size of 3+.
    reqs = [req("must", "moderate")] + [req("nice", "strong")] * 3
    _, tier = score_job(reqs, {}, total_years=5.0)
    assert tier == "apply"


def test_empty_requirements_is_none_not_a_perfect_score():
    # Zero extracted requirements means extraction likely failed, must
    # never be silently treated as a perfect match.
    fit, tier = score_job([], {}, total_years=5.0)
    assert (fit, tier) == (None, None)


def test_years_cap_demotes_apply_to_stretch_on_a_big_must_have_gap():
    # A must-have failing by more than ~1.5x caps the tier at stretch
    # regardless of how strong the match itself was rated.
    reqs = [req("must", "strong", skill_key="kubernetes", years_required=6.0)]
    fit, tier = score_job(reqs, {"kubernetes": 1.0}, total_years=5.0)
    assert tier == "stretch"


def test_years_cap_does_not_demote_a_small_gap():
    # 4.0 years have vs 6.0 required is within the ~1.5x grace band
    # (6.0 / 1.5 = 4.0), so this should NOT get capped.
    reqs = [req("must", "strong", skill_key="kubernetes", years_required=6.0)]
    fit, tier = score_job(reqs, {"kubernetes": 4.0}, total_years=5.0)
    assert tier == "apply"


def test_years_check_falls_back_to_total_years_when_no_skill_key():
    # "5+ years of software engineering experience" has no skill_key to look
    # up in skill_years, must fall back to total professional years rather
    # than silently skip the check.
    reqs = [req("must", "strong", skill_key=None, years_required=8.0)]
    fit, tier = score_job(reqs, {}, total_years=2.0)
    assert tier == "stretch"


def test_match_strength_weights_are_not_binary():
    # A "weak" match must count for meaningfully less than a "strong" one,
    # this is the whole point of migration 007 over the old matched/
    # unmatched binary.
    strong_fit, _ = score_job([req("must", "strong")], {}, total_years=5.0)
    weak_fit, _ = score_job([req("must", "weak")], {}, total_years=5.0)
    assert strong_fit > weak_fit
