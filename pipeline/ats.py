import re

ATS_PATTERNS = {
    "greenhouse":      "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever":           "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby":           "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{slug}/postings",
    "workable":        "https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
}

# Ordered by how common each ATS is, so most companies resolve in 1-2 requests.
PROBE_ORDER = ["greenhouse", "lever", "ashby", "workable", "smartrecruiters"]

_JOB_LIST_KEY = {
    "greenhouse":      "jobs",
    "ashby":           "jobs",
    "workable":        "jobs",
    "smartrecruiters": "content",
}

_SUFFIXES = {
    "inc", "llc", "ltd", "corp", "corporation", "co", "company",
    "technologies", "technology", "labs", "lab", "software",
    "group", "holdings", "systems",
}

# These 404 for unknown slugs, so a 200 with zero jobs is trustworthy —
# a real board with nothing open right now.
STRICT_404 = {"greenhouse", "lever", "ashby"}

def count_jobs(ats: str, data) -> int:
    """Number of postings in a response. Absorbs the shape differences."""
    if ats == "lever":
        return len(data) if isinstance(data, list) else 0
    if not isinstance(data, dict):
        return 0
    jobs = data.get(_JOB_LIST_KEY[ats])
    return len(jobs) if isinstance(jobs, list) else 0


def slug_candidates(name: str) -> list[str]:
    """Plausible slugs for a company name, most likely first."""
    cleaned = name.lower().strip().replace("&", " and ")
    cleaned = re.sub(r"[^a-z0-9\s-]", "", cleaned)
    words = [w for w in cleaned.replace("-", " ").split() if w]

    while words and words[-1] in _SUFFIXES:
        words.pop()
    if not words:
        return []

    nospace = "".join(words)
    out = [nospace, "-".join(words), nospace + "hq", words[0]]
    if words[0] == "the" and len(words) > 1:
        out.insert(1, "".join(words[1:]))

    seen, uniq = set(), []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq
