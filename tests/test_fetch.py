"""Regression tests for pipeline.fetch's per-ATS parsers, pure functions
(dict/list in, NormalizedJob out), no network. This is the highest-risk
previously-untested area given PROJECT.md §5's own "quirks" list is a
record of real bugs found here: SmartRecruiters' `ref` field is sometimes a
plain string not an object (crashed two companies), Ashby compensation
components need type/interval filtering, location is often a nested object
or a list-shaped string. Each fixture below is shaped like the real
response, not a simplified stand-in.
"""
from pipeline.fetch import (
    parse_ashby,
    parse_greenhouse,
    parse_lever,
    parse_smartrecruiters,
    parse_workable,
)


def test_parse_greenhouse_strips_html_and_extracts_nested_location():
    data = {"jobs": [{
        "id": 123, "title": "Senior Software Engineer",
        "location": {"name": "Remote, US"},
        "content": "<p>We need a <b>great</b> engineer.</p>",
        "absolute_url": "https://example.com/jobs/123",
    }]}
    jobs = list(parse_greenhouse(data))
    assert len(jobs) == 1
    j = jobs[0]
    assert j.source_job_id == "123"
    assert j.location == "Remote, US"
    assert j.description == "We need a great engineer."
    assert j.remote is True


def test_parse_greenhouse_empty_board_yields_nothing():
    assert list(parse_greenhouse({"jobs": []})) == []


def test_parse_lever_prefers_description_plain_over_html():
    data = [{
        "id": "abc", "text": "SDET", "categories": {"location": "Remote", "commitment": "Full-time"},
        "descriptionPlain": "Plain text description.",
        "description": "<p>HTML description.</p>",
        "lists": [{"text": "Requirements", "content": "<ul><li>Python</li></ul>"}],
        "hostedUrl": "https://jobs.lever.co/x/abc",
    }]
    jobs = list(parse_lever(data))
    j = jobs[0]
    assert "Plain text description." in j.description
    assert "HTML description." not in j.description
    assert "Requirements: Python" in j.description


def test_parse_lever_falls_back_to_stripped_html_when_plain_is_missing():
    data = [{
        "id": "abc", "text": "SDET", "categories": {"location": "Remote"},
        "descriptionPlain": None, "description": "<p>Only HTML here.</p>",
        "lists": [], "hostedUrl": None,
    }]
    j = list(parse_lever(data))[0]
    assert j.description == "Only HTML here."


def test_parse_ashby_only_takes_annual_salary_not_hourly():
    # PROJECT.md §5: Ashby compensation components can be typed "Salary"
    # with an hourly interval, an unguarded parser would store the hourly
    # figure in a field compared against an annual floor.
    data = {"jobs": [{
        "id": "j1", "title": "Engineer", "location": "Remote", "isRemote": True,
        "descriptionHtml": "<p>Job details.</p>",
        "compensation": {"summaryComponents": [
            {"compensationType": "Salary", "interval": "1 HOUR", "minValue": 75, "maxValue": 90, "currencyCode": "USD"},
            {"compensationType": "Salary", "interval": "1 YEAR", "minValue": 130000, "maxValue": 160000, "currencyCode": "USD"},
        ]},
    }]}
    j = list(parse_ashby(data))[0]
    assert (j.comp_min, j.comp_max, j.comp_currency) == (130000, 160000, "USD")


def test_parse_ashby_null_min_value_component_is_skipped():
    data = {"jobs": [{
        "id": "j1", "title": "Engineer", "location": None, "isRemote": None,
        "descriptionHtml": None,
        "compensation": {"summaryComponents": [
            {"compensationType": "Salary", "interval": "1 YEAR", "minValue": None, "maxValue": None},
        ]}},
    ]}
    j = list(parse_ashby(data))[0]
    assert (j.comp_min, j.comp_max, j.comp_currency) == (None, None, None)


def test_parse_ashby_no_compensation_block_does_not_crash():
    data = {"jobs": [{"id": "j1", "title": "Engineer", "location": None, "isRemote": None}]}
    j = list(parse_ashby(data))[0]
    assert j.comp_min is None


def test_parse_smartrecruiters_ref_as_plain_string_does_not_crash():
    # PROJECT.md §5: SmartRecruiters returns `ref` as a plain URL string,
    # not an object, this crashed two companies before the isinstance guard.
    data = {"content": [{
        "id": "sr1", "name": "SDET",
        "location": {"city": "Portland", "region": "OR", "country": "US", "remote": False},
        "ref": "https://jobs.smartrecruiters.com/x/sr1",
    }]}
    j = list(parse_smartrecruiters(data))[0]
    assert j.apply_url == "https://jobs.smartrecruiters.com/x/sr1"
    assert j.location == "Portland, OR, US"
    assert j.remote is False


def test_parse_smartrecruiters_ref_as_object():
    data = {"content": [{
        "id": "sr1", "name": "SDET", "location": "Remote",
        "ref": {"jobAd": "https://jobs.smartrecruiters.com/x/sr1", "landingPage": "https://x.com"},
    }]}
    j = list(parse_smartrecruiters(data))[0]
    assert j.apply_url == "https://jobs.smartrecruiters.com/x/sr1"
    assert j.location == "Remote"  # bare string location, not a dict


def test_parse_smartrecruiters_description_is_always_none():
    # Not in the list endpoint per PROJECT.md §5, must not silently invent one.
    data = {"content": [{"id": "sr1", "name": "SDET", "location": None, "ref": None}]}
    j = list(parse_smartrecruiters(data))[0]
    assert j.description is None


def test_parse_workable_joins_city_state_country_into_one_location():
    data = {"jobs": [{
        "shortcode": "wk1", "title": "Engineer",
        "city": "Portland", "state": "OR", "country": "United States",
        "telecommuting": True, "description": "<p>Details.</p>",
        "url": "https://apply.workable.com/x/wk1",
    }]}
    j = list(parse_workable(data))[0]
    assert j.location == "Portland, OR, United States"
    assert j.remote is True
    assert j.source_job_id == "wk1"


def test_parse_workable_falls_back_to_id_when_shortcode_missing():
    data = {"jobs": [{"id": 999, "title": "Engineer", "telecommuting": None, "description": None}]}
    j = list(parse_workable(data))[0]
    assert j.source_job_id == "999"
