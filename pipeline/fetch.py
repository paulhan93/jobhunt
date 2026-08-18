import time
from typing import Iterator

import httpx

from pipeline.ats import ATS_PATTERNS
from pipeline.models import NormalizedJob
from pipeline.text import REMOTE_RE, strip_html

HEADERS = {"User-Agent": "jobhunt/0.1 (paul@example.com)"}
TIMEOUT = 30.0


def looks_remote(*parts: str | None) -> bool | None:
    joined = " ".join(p for p in parts if p)
    return bool(REMOTE_RE.search(joined)) if joined else None


# --- per-ATS parsers ------------------------------------------------------
# We use yield instead of return in these functions because yield pause's a
# function's execution and returns a value temporarily. Unlike return which
# terminates the function completely, yield saves the function's local state,
# variables, and execution pointer, allowing the function to resume exactly
# where it left off when the next value is requested.

def parse_greenhouse(data: dict) -> Iterator[NormalizedJob]:
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name")
        yield NormalizedJob(
            source="greenhouse",
            source_job_id=str(j["id"]),
            title=j["title"],
            location=loc,
            remote=looks_remote(loc, j.get("title")),
            description=strip_html(j.get("content")),
            apply_url=j.get("absolute_url"),
            raw=j,
        )


def parse_lever(data: list) -> Iterator[NormalizedJob]:
    for j in data:
        cat = j.get("categories") or {}
        loc = cat.get("location")
        body = j.get("descriptionPlain") or strip_html(j.get("description"))
        lists = " ".join(
            f"{s.get('text', '')}: {strip_html(s.get('content')) or ''}"
            for s in (j.get("lists") or [])
        )
        yield NormalizedJob(
            source="lever",
            source_job_id=str(j["id"]),
            title=j["text"],
            location=loc,
            remote=looks_remote(loc, cat.get("commitment"), j.get("workplaceType")),
            description=" ".join(p for p in (body, lists) if p) or None,
            apply_url=j.get("hostedUrl"),
            raw=j,
        )


def parse_ashby(data: dict) -> Iterator[NormalizedJob]:
    for j in data.get("jobs", []):
        comp_min = comp_max = None
        currency = None
        for c in (j.get("compensation") or {}).get("summaryComponents") or []:
            if (c.get("compensationType") == "Salary"
                    and c.get("interval") == "1 YEAR"
                    and c.get("minValue") is not None):
                comp_min = c.get("minValue")
                comp_max = c.get("maxValue")
                currency = c.get("currencyCode")
                break
        yield NormalizedJob(
            source="ashby",
            source_job_id=str(j["id"]),
            title=j["title"],
            location=j.get("location"),
            remote=j.get("isRemote"),
            description=strip_html(j.get("descriptionHtml")) or j.get("descriptionPlain"),
            apply_url=j.get("jobUrl") or j.get("applyUrl"),
            comp_min=comp_min,
            comp_max=comp_max,
            comp_currency=currency,
            raw=j,
        )


def parse_smartrecruiters(data: dict) -> Iterator[NormalizedJob]:
    for j in data.get("content", []):
        loc_obj = j.get("location")
        if isinstance(loc_obj, dict):
            loc = ", ".join(
                p for p in (loc_obj.get("city"), loc_obj.get("region"),
                            loc_obj.get("country"))
                if p
            ) or None
            remote = loc_obj.get("remote")
        else:
            loc = loc_obj if isinstance(loc_obj, str) else None
            remote = None

        ref = j.get("ref")
        if isinstance(ref, dict):
            apply_url = ref.get("jobAd") or ref.get("landingPage")
        elif isinstance(ref, str):
            apply_url = ref
        else:
            apply_url = None

        yield NormalizedJob(
            source="smartrecruiters",
            source_job_id=str(j["id"]),
            title=j["name"],
            location=loc,
            remote=remote,
            description=None,   # not in the list endpoint
            apply_url=apply_url or j.get("applyUrl"),
            raw=j,
        )


def parse_workable(data: dict) -> Iterator[NormalizedJob]:
    for j in data.get("jobs", []):
        loc = ", ".join(
            p for p in (j.get("city"), j.get("state"), j.get("country")) if p
        ) or None
        yield NormalizedJob(
            source="workable",
            source_job_id=str(j.get("shortcode") or j["id"]),
            title=j["title"],
            location=loc,
            remote=j.get("telecommuting"),
            description=strip_html(j.get("description")),
            apply_url=j.get("url") or j.get("application_url"),
            raw=j,
        )


PARSERS = {
    "greenhouse": parse_greenhouse,
    "lever": parse_lever,
    "ashby": parse_ashby,
    "smartrecruiters": parse_smartrecruiters,
    "workable": parse_workable,
}


# --- fetching ------------------------------------------------------------

def _get(client: httpx.Client, url: str) -> dict | list | None:
    last = None
    for attempt in range(3):
        try:
            r = client.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                last = RuntimeError(f"http {r.status_code}")
            else:
                raise RuntimeError(f"http {r.status_code}")
        except httpx.RequestError as e:
            last = e
        time.sleep(2 ** attempt)
    raise last


def fetch_board(client: httpx.Client, ats: str, slug: str) -> list[NormalizedJob]:
    """Fetch one company's whole board, paging where the ATS requires it."""
    if ats == "greenhouse":
        url = ATS_PATTERNS[ats].format(slug=slug) + "?content=true"
        return list(parse_greenhouse(_get(client, url)))

    if ats == "smartrecruiters":
        jobs, offset = [], 0
        while True:
            url = f"{ATS_PATTERNS[ats].format(slug=slug)}?limit=100&offset={offset}"
            data = _get(client, url)
            page = list(parse_smartrecruiters(data))
            jobs.extend(page)
            offset += 100
            if len(page) < 100 or offset >= data.get("totalFound", 0):
                break
        return jobs

    url = ATS_PATTERNS[ats].format(slug=slug)
    return list(PARSERS[ats](_get(client, url)))
