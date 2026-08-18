"""Regression tests for pipeline.extract.extract_comp(), pure regex, no
network, no model. Two of these are direct regression tests for real misses
found in production JDs (DECISIONS.md #42 and #51) that a hand-picked test
before shipping would have caught immediately.
"""
from pipeline.extract import extract_comp


def test_usd_suffix_on_each_number_with_and_connector():
    # DECISIONS.md #42, the regex's own motivating example: the first
    # version only handled USD as a suffix on the RANGE's end, not on each
    # number individually.
    lo, hi, cur = extract_comp("Compensation: $113,000 USD and $158,000 USD annually.")
    assert (lo, hi, cur) == (113000.0, 158000.0, "USD")


def test_usd_prefix_with_no_dollar_sign():
    # DECISIONS.md #51, a real Grafana Labs posting used USD as a prefix
    # with no $ at all ("USD 127,651 - USD 203,867"), which the
    # suffix-only version of the regex missed entirely.
    lo, hi, cur = extract_comp("The salary range is USD 127,651 - USD 203,867 per year.")
    assert (lo, hi, cur) == (127651.0, 203867.0, "USD")


def test_prefers_the_comp_context_window_over_an_unrelated_number_pair():
    # A JD can state other dollar ranges (funding, revenue), extract_comp
    # must anchor on the salary/compensation keyword's window, not just grab
    # the first number pair anywhere in the text.
    text = ("We closed a $50,000 - $75,000 funding round last year. "
            "The salary range for this role is $130,000 - $150,000 based on experience.")
    lo, hi, cur = extract_comp(text)
    assert (lo, hi, cur) == (130000.0, 150000.0, "USD")


def test_rejects_a_range_below_the_plausible_salary_floor():
    # Guards against grabbing something that isn't a plausible annual salary
    # (a day rate, a contract-hours figure, ...).
    lo, hi, cur = extract_comp("The pay range is 10,000 - 15,000 for this contract role.")
    assert (lo, hi, cur) == (None, None, None)


def test_swaps_a_reversed_range():
    lo, hi, cur = extract_comp("Salary: $180,000 - $150,000 depending on level.")
    assert (lo, hi, cur) == (150000.0, 180000.0, "USD")


def test_no_comp_mentioned_returns_all_none():
    lo, hi, cur = extract_comp("We are a fast-growing startup building great things.")
    assert (lo, hi, cur) == (None, None, None)
