"""Regression tests for pipeline.filters.classify(), pure function, no DB,
no network, no model. Each test below is tied to a specific case documented
in DECISIONS.md as having broken (and been fixed) before; the point of these
tests is that the next retune of _FAMILIES/_REJECTS can't silently
reintroduce one without a red test telling you so.

New rule going forward: when a real classify() bug gets found and fixed
(this has happened repeatedly per DECISIONS.md), add its regression test
here in the same change, not as a followup.
"""
from pipeline.filters import _build_keyword_pattern, classify


def test_solutions_architect_is_customer_eng_not_a_seniority_reject(job):
    # DECISIONS.md #62, folded into customer_eng, not rejected as "architect".
    status, family, reason = classify(job(title="Solutions Architect", location="Remote, US"))
    assert (status, family, reason) == ("filtered", "customer_eng", None)


def test_bare_architect_is_still_too_senior(job):
    status, family, reason = classify(job(title="Enterprise Architect", location="Remote, US"))
    assert (status, family, reason) == ("rejected", None, "seniority_too_high")


def test_finance_titled_engineer_role_still_passes(job):
    # PROJECT.md §6, the hard/soft not_engineering split this protects:
    # a real engineering title shouldn't reject just because a department
    # word ("Finance") also appears in it.
    status, family, reason = classify(
        job(title="Software Engineer, Finance Applications", location="Remote, US")
    )
    assert (status, family, reason) == ("filtered", "swe", None)


def test_enterprise_account_executive_observability_is_rejected_not_sre(job):
    # PROJECT.md §6, the exact regression the hard/soft split was built to
    # fix: "observability" is sre's family keyword, but "Account Executive"
    # must win regardless of what product line is in the title.
    status, family, reason = classify(
        job(title="Enterprise Account Executive, Observability", location="Remote, US")
    )
    assert (status, family, reason) == ("rejected", None, "not_engineering")


def test_customer_engineer_developer_platform_is_customer_eng_not_platform(job):
    # PROJECT.md §6, family ordering: customer-facing beats a product-area
    # word ("developer platform") that would otherwise tag this `platform`.
    status, family, reason = classify(
        job(title="Senior Customer Engineer - Developer Platform", location="Remote, US")
    )
    assert (status, family, reason) == ("filtered", "customer_eng", None)


def test_bare_frontend_engineer_is_swe_not_no_family_match(job):
    # DECISIONS.md #60, the allowlist fix for titles like "Frontend
    # Engineer" that don't contain the literal substring "software engineer".
    status, family, reason = classify(job(title="Frontend Engineer", location="Remote, US"))
    assert (status, family, reason) == ("filtered", "swe", None)


def test_software_development_engineer_is_swe(job):
    # DECISIONS.md #72, the Amazon/AWS-style "SDE" title, which doesn't
    # contain "software engineer" or "software developer" as a substring.
    status, family, reason = classify(
        job(title="Software Development Engineer II", location="Remote, US")
    )
    assert (status, family, reason) == ("filtered", "swe", None)


def test_manual_qa_is_rejected_regardless_of_seniority(job):
    status, family, reason = classify(job(title="Manual QA Tester", location="Remote, US"))
    assert (status, family, reason) == ("rejected", None, "manual_qa")


def test_staff_is_its_own_reject_reason_not_lumped_into_too_high(job):
    # PROJECT.md §2, staff gets its own reason so the bucket stays
    # reviewable; it must not fall into seniority_too_high instead.
    status, family, reason = classify(job(title="Staff Software Engineer", location="Remote, US"))
    assert (status, family, reason) == ("rejected", None, "seniority_staff")


def test_comp_below_floor_only_fires_when_comp_data_exists(job):
    # PROJECT.md §6, "bias toward passing when evidence is absent": no comp
    # data means the check has nothing to evaluate and must not reject.
    status, family, reason = classify(
        job(title="Senior Software Engineer", location="Remote, US",
            comp_min=None, comp_max=None)
    )
    assert (status, family, reason) == ("filtered", "swe", None)


def test_comp_below_floor_uses_max_not_min_for_a_range_that_overlaps_floor(job):
    # PROJECT.md §6, a 108k-145k range must pass: comp_max clears the
    # 130k floor even though comp_min doesn't.
    status, family, reason = classify(
        job(title="Senior Software Engineer", location="Remote, US",
            comp_min=108_000, comp_max=145_000)
    )
    assert (status, family, reason) == ("filtered", "swe", None)


def test_comp_below_floor_rejects_when_the_whole_range_is_under(job):
    status, family, reason = classify(
        job(title="Senior Software Engineer", location="Remote, US",
            comp_min=95_000, comp_max=110_000)
    )
    assert (status, family, reason) == ("rejected", "swe", "comp_below_floor")


def test_multi_location_string_accepts_if_any_option_is_acceptable(job):
    # PROJECT.md §5, location is often a list; an acceptable option
    # anywhere in the string must win even alongside foreign options.
    status, family, reason = classify(job(
        title="Senior Software Engineer",
        location="San Francisco, CA, New York, NY, Portland, OR, "
                 "or Remote within Canada or United States",
    ))
    assert (status, family, reason) == ("filtered", "swe", None)


def test_foreign_only_location_is_rejected(job):
    status, family, reason = classify(
        job(title="Senior Software Engineer", location="Berlin, Germany")
    )
    assert (status, family, reason) == ("rejected", "swe", "location")


def test_title_only_foreign_signal_is_caught_even_with_generic_remote_location(job):
    # PROJECT.md §6, the fix for a title-only foreign signal ("Shenzhen")
    # slipping through when the location field is just a country-agnostic
    # "Remote"/"Distributed" with no location-based reject to catch it.
    status, family, reason = classify(job(
        title="Senior Customer Engineer, Shenzhen",
        location="Remote",
    ))
    assert (status, family, reason) == ("rejected", "customer_eng", "location")


# _build_keyword_pattern() backs COMP_FLOOR/_PORTLAND's preferences.yaml
# wiring (DECISIONS.md #79), moved from a hardcoded regex to a pattern
# built from resume.yaml's onsite_metros list, verified byte-for-byte
# identical against the full real corpus (7,690 jobs, 0 diffs) when this
# landed, these tests are the ongoing regression guard for that.

def test_build_keyword_pattern_matches_a_bare_single_word_term():
    p = _build_keyword_pattern(["Portland"])
    assert p.search("Portland, OR")
    assert p.search("Portland, Oregon")
    assert not p.search("Portlandia")  # word-boundaried: a substring isn't a match


def test_build_keyword_pattern_multiword_term_requires_both_words():
    # "Vancouver, WA" must NOT match a bare "Vancouver" (ambiguous with
    # Vancouver, BC, which _FOREIGN rejects elsewhere), this is exactly
    # why it's its own two-word entry instead of a bare "Vancouver".
    p = _build_keyword_pattern(["Vancouver, WA"])
    assert p.search("Vancouver, WA")
    assert p.search("Vancouver WA")  # flexible separator
    assert not p.search("Vancouver, BC")
    assert not p.search("Vancouver")


def test_build_keyword_pattern_empty_list_never_matches():
    p = _build_keyword_pattern([])
    assert not p.search("Portland, OR")
    assert not p.search("")
