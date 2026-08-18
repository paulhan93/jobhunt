import re
import sqlite3

from pipeline import resume_bank
from pipeline.text import REMOTE_RE

# Comp floor and acceptable onsite metros are personal preference VALUES, so
# they're read from resume.yaml's `preferences` block (gitignored,
# human-edited) rather than hardcoded here, unlike the classification logic
# below (which titles count as which role_family), which stays in code on
# purpose since it's real matching logic with ordering/precedence, not a
# personal value, and it's what tests/test_filters.py protects. Both fall
# back to the values that used to be hardcoded here if resume.yaml is
# missing or has no preferences block, so classify() still works on a
# from-scratch checkout with no resume.yaml yet. See PROJECT.md's backlog
# entry ("Wire up resume.yaml's preferences block") and DECISIONS.md #79.
_prefs = resume_bank.preferences()

_DEFAULT_ONSITE_METROS = [
    "Portland", "Beaverton", "Hillsboro", "Tigard", "Vancouver, WA", "Oregon",
]


def _build_keyword_pattern(terms: list[str]) -> re.Pattern:
    """Case-insensitive alternation over free-text keywords/phrases from
    preferences.onsite_metros. Each term's words are escaped individually and
    joined with a flexible separator (so a YAML entry like "Vancouver, WA"
    still matches "Vancouver WA" in a real location string), the whole term
    wrapped in word boundaries. An empty term list compiles to a pattern that
    never matches, not one that matches everything."""
    parts = []
    for t in terms:
        words = [w for w in re.split(r"[\s,]+", t.strip()) if w]
        if not words:
            continue
        parts.append(r"\b" + r"[\s,]+".join(re.escape(w) for w in words) + r"\b")
    return re.compile("|".join(parts), re.I) if parts else re.compile(r"(?!)")


# --- Comp: USD -----------------------------------------------------------
COMP_FLOOR = _prefs.get("comp_floor", 130_000)          # only applied when comp data exists

# --- location ------------------------------------------------------------

_PORTLAND = _build_keyword_pattern(_prefs.get("onsite_metros") or _DEFAULT_ONSITE_METROS)

_US = re.compile(r"\bunited states\b|\bu\.?s\.?a?\b|\bus\b", re.I)

# Anywhere I can't work. Only ever used negatively.
_FOREIGN = re.compile(
    r"\b(emea|apac|latam|india|europe|uk|united kingdom|canada|australia"
    r"|germany|poland|brazil|mexico|japan|singapore|ireland|serbia|cyprus"
    r"|spain|czech|armenia|france|netherlands|sweden|norway|denmark|finland"
    r"|switzerland|austria|portugal|italy|romania|bulgaria|ukraine|israel"
    r"|china|korea|taiwan|philippines|vietnam|thailand|indonesia|malaysia"
    r"|argentina|colombia|chile|peru|south africa|nigeria|kenya|egypt|uae"
    r"|dubai|toronto|vancouver, bc|montreal|ottawa|ontario|quebec"
    r"|bengaluru|bangalore|hyderabad|pune|mumbai|delhi|chennai|noida"
    r"|berlin|munich|hamburg|madrid|barcelona|lisbon|paris|amsterdam"
    r"|warsaw|krakow|prague|budapest|bucharest|belgrade|sofia|zagreb"
    r"|dublin|london|manchester|edinburgh|glasgow|belfast|zurich|geneva"
    r"|stockholm|oslo|copenhagen|helsinki|vienna|brussels|milan|rome"
    r"|tokyo|osaka|seoul|beijing|shanghai|shenzhen|hong kong|taipei"
    r"|sydney|melbourne|brisbane|auckland|wellington|yerevan|tbilisi"
    r"|sao paulo|mexico city|bogota|buenos aires|santiago|lima)\b",
    re.I,
)


def location_ok(job) -> bool:
    """True if I could actually take this job from Portland."""
    loc = job["location"] or ""

    # The location field is often a generic "Distributed"/"Remote (US)" with
    # no country restriction at all, while the actual sales/account territory
    # (frequently foreign - "Senior Customer Engineer, Shenzhen") only shows
    # up in the title. _FOREIGN was only ever checked against `loc`, so a
    # title-only foreign signal slipped through every branch below. Doesn't
    # catch a bare foreign city with no country word in the title either
    # (e.g. "Shenzhen" alone) - that would need a city gazetteer, out of
    # scope for this fix.
    title_foreign = bool(_FOREIGN.search(job["title"] or ""))

    if not loc:
        return bool(job["remote"]) and not title_foreign

    # An acceptable option anywhere in the string wins, even if the string also
    # lists foreign locations — these fields are often multi-location lists,
    # e.g. "Remote | US or Canada" or "... Portland, OR, or Remote within
    # Canada or United States".
    if _PORTLAND.search(loc):
        return True
    if REMOTE_RE.search(loc) and _US.search(loc):
        return not title_foreign

    # Bare "Remote" with no country named: accept unless a foreign place appears.
    if REMOTE_RE.search(loc):
        return not _FOREIGN.search(loc) and not title_foreign

    # The ATS remote flag alone isn't trustworthy — observed remote=1 on
    # onsite-only SF postings — so also require an acceptable location token.
    return (bool(job["remote"])
            and bool(_US.search(loc) or _PORTLAND.search(loc))
            and not _FOREIGN.search(loc)
            and not title_foreign)


# --- reject patterns (checked in order; first match wins) ----------------
# not_engineering is split into two tiers, not one list — see classify().
#
# _HARD_NOT_ENGINEERING is checked before family matching, same as seniority.
# These phrases are never ambiguous: a title saying "Account Executive" or
# "BDR" is that job, full stop, regardless of what other tech buzzwords
# appear in it. Checking these unconditionally matters because some family
# patterns match on bare product-area/domain words (e.g. sre's "observability",
# customer_eng's "technical account") that can appear inside a real AE/BDR
# title — "Enterprise Account Executive, Observability" must not become an
# sre match just because "observability" is the product line being sold.
#
# _SOFT_NOT_ENGINEERING is checked only when no family matched. These are
# department/domain words (marketing, finance, revenue, ...) that legitimately
# qualify a real engineering title too, so a family match should win over
# them. Verified against real rejects — this rescues titles like "Software
# Engineer, Finance Applications" and "Sales Engineer (Customer Success)"
# that a single unconditional list was silently killing, while titles that
# truly don't match any family (e.g. "Technical Support Engineer") still
# correctly reject here exactly as before.

_HARD_NOT_ENGINEERING = re.compile(
    r"\b(account executive|account manager|recruit|talent|counsel|hr"
    r"|program manager|project manager|analyst|partnership"
    r"|business development|bdr|sdr|solutions consultant"
    r"|technical writer|data scientist|research scientist)\b", re.I)

_SOFT_NOT_ENGINEERING = re.compile(
    r"\b(marketing|customer success|support|finance|accounting|legal"
    r"|people|communications|content|brand|design|designer|operations"
    r"|revenue)\b", re.I)

_REJECTS = [
    ("not_engineering", _HARD_NOT_ENGINEERING),

    ("seniority_too_low", re.compile(
        r"\b(intern|internship|new grad|graduate|junior|jr\.?|associate"
        r"|apprentice|entry.level|co.?op)\b", re.I)),

    # "architect" excludes "solutions/solution architect" — that title is
    # folded into customer_eng (same track as sales engineer), not treated as
    # a too-senior title. Plain "Architect"/"Enterprise Architect"/etc. still
    # rejects here as intended.
    # Bare "management" removed (was matching as a domain noun inside titles
    # like "Senior Software Engineer - Identity & Access Management", not
    # just people-manager titles); "manager" alone still catches those.
    ("seniority_too_high", re.compile(
        r"\b(principal|distinguished|fellow"
        r"|(?<!solutions )(?<!solution )architect|vp|vice president"
        r"|director|head of|chief|cto|(?<!product )manager)\b", re.I)),

    ("seniority_staff", re.compile(r"\bstaff\b", re.I)),

    ("manual_qa", re.compile(
        r"\b(manual (qa|test)|qa analyst|qa tester|game tester"
        r"|localization|performance tester)\b", re.I)),
]


# --- role families (ordered: first match wins) ---------------------------
# Order matters here. First match wins.
# tpm is FIRST: it must claim technical/platform-flavored Product Manager
# titles before "platform" or "customer_eng" can steal them on a keyword like
# "developer platform". Plain "Product Manager" with no technical-context word
# nearby stays unmatched here and falls through to no_family_match — this
# family is scoped to Technical Product Manager specifically, not generalist
# PM (out of scope, see PROJECT.md §2).
# customer_eng is SECOND: customer-facing is a stronger signal than any
# product-area word. Without this, "Senior Customer Engineer - Developer
# Platform" gets tagged platform because "developer platform" matches.
# ai_eng comes after platform/customer_eng so it doesn't steal titles those
# stronger signals should own (e.g. "Forward Deployed AI Engineer" stays
# customer_eng). Its pattern is narrow on purpose — generic "platform
# engineer"-style titles fall through to sre as before, unchanged.

_FAMILIES = [
    ("tpm", re.compile(
        r"\btechnical product manager\b"
        r"|\bproduct manager\b.*\b(developer|platform|api|infrastructure"
        r"|internal tools|engineering|technical)\b"
        r"|\b(developer|platform|api|infrastructure|internal tools|engineering"
        r"|technical)\b.*\bproduct manager\b",
        re.I)),

    ("customer_eng", re.compile(
        r"\b(forward deployed|fde|solutions? engineer|solutions? architect"
        r"|customer engineer|presales|pre.sales|partner engineer"
        r"|account engineer|sales engineer|developer advocate"
        r"|technical account)\b", re.I)),

    ("platform", re.compile(
        r"developer productivity|developer experience|engineering productivity"
        r"|platform productivity|dev ?ex\b|dev ?prod\b"
        r"|developer infra(structure)?|developer platform|developer tools?\b"
        r"|build (engineer|systems|infra)|release engineer"
        r"|internal tool|tooling"
        r"|test infra(structure)?|test platform|test enabler"
        r"|productivity engineer|ci/?cd",
        re.I)),

    ("ai_eng", re.compile(
        r"\bai engineer\b|\bagentic\b|\bai automation\b|\bllm engineer\b",
        re.I)),

    ("sdet", re.compile(
        r"\bsdet\b|software (development )?engineer in test"
        r"|test automation|automation engineer|test engineer"
        r"|quality engineer|\bqa engineer\b|quality assurance engineer",
        re.I)),

    ("sre", re.compile(
        r"site reliability|\bsre\b|infrastructure engineer|platform engineer"
        r"|observability|devops"
        # Added 2026-08-17 after auditing the no_family_match bucket:
        # "Database Reliability Engineer" / "Network Reliability Engineer"
        # style titles were falling through since only "site reliability"
        # was covered, not a bare "reliability engineer". See DECISIONS.md
        # #72.
        r"|reliability engineer",
        re.I)),

    ("swe", re.compile(
        r"software engineer|backend engineer|back.end engineer|full ?stack"
        r"|swe\b|software developer"
        # Broadened 2026-08-16: swe originally required "software
        # engineer"/"backend engineer"/etc. verbatim, so a bare "Frontend
        # Engineer" or "iOS Engineer" title matched no family at all and
        # silently fell through to no_family_match. Added explicit
        # allowlist for the specific engineering specialties this was
        # dropping, rather than a bare `\bengineer\b` catch-all, tested
        # against the full rejected set and a bare-word version pulled in
        # 214 titles including IT Systems Engineer, Field Engineer,
        # Consulting Engineer, Firmware Engineer, Detection Engineer, GTM
        # Engineer, noise the project explicitly wants kept out.
        r"|frontend engineer|front.end engineer|product engineer"
        r"|distributed systems engineer|android engineer|ios engineer"
        r"|mobile engineer|analytics engineer|\bapi engineer\b"
        # Added 2026-08-17: "Software Development Engineer" (the Amazon/AWS
        # style SDE title) doesn't contain the substring "software engineer"
        # or "software developer", so it matched nothing. See DECISIONS.md
        # #72.
        r"|software development engineer",
        re.I)),
]


def normalize(title: str) -> str:
    """Lowercase, expand abbreviations, collapse punctuation and whitespace."""
    t = title.lower()
    t = t.replace("sr.", "senior").replace("sr ", "senior ")
    t = re.sub(r"[/,()\[\]|–—-]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def classify(job: sqlite3.Row) -> tuple[str, str | None, str | None]:
    """Returns (status, role_family, reject_reason)."""
    title = normalize(job["title"])

    for name, pattern in _REJECTS:
        if pattern.search(title):
            return "rejected", None, name

    family = None
    for fam, pattern in _FAMILIES:
        if pattern.search(title):
            family = fam
            break

    if family is None:
        if _SOFT_NOT_ENGINEERING.search(title):
            return "rejected", None, "not_engineering"
        return "rejected", None, "no_family_match"

    if not location_ok(job):
        return "rejected", family, "location"

    # Check comp_max, not comp_min: a range like 108k-145k has comp_min below
    # the floor but clearly overlaps it. Reject only when the whole range (or
    # the only figure given) is below floor. Falls back to comp_min when max
    # is null (single-figure postings, or old rows fetched before comp_max
    # existed).
    comp_ceiling = job["comp_max"] if job["comp_max"] is not None else job["comp_min"]
    if comp_ceiling is not None and comp_ceiling < COMP_FLOOR:
        return "rejected", family, "comp_below_floor"

    return "filtered", family, None
