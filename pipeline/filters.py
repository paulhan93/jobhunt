import re
import sqlite3


# --- Comp: USD -----------------------------------------------------------
COMP_FLOOR = 130_000          # only applied when comp data exists

# --- location ------------------------------------------------------------

_REMOTE = re.compile(r"\bremote\b|\banywhere\b|\bdistributed\b|\bwork from home\b", re.I)

_PORTLAND = re.compile(r"\bportland\b|\bbeaverton\b|\bhillsboro\b|\btigard\b"
                       r"|\bvancouver,?\s*wa\b|\boregon\b", re.I)

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

    if not loc:
        return bool(job["remote"])

    # An acceptable option anywhere in the string wins, even if the string also
    # lists foreign locations — these fields are often multi-location lists,
    # e.g. "Remote | US or Canada" or "... Portland, OR, or Remote within
    # Canada or United States".
    if _PORTLAND.search(loc):
        return True
    if _REMOTE.search(loc) and _US.search(loc):
        return True

    # Bare "Remote" with no country named: accept unless a foreign place appears.
    if _REMOTE.search(loc):
        return not _FOREIGN.search(loc)

    # The ATS remote flag alone isn't trustworthy — observed remote=1 on
    # onsite-only SF postings — so also require an acceptable location token.
    return (bool(job["remote"])
            and bool(_US.search(loc) or _PORTLAND.search(loc))
            and not _FOREIGN.search(loc))


# --- reject patterns (checked in order; first match wins) ----------------

_REJECTS = [
    ("not_engineering", re.compile(
        r"\b(account executive|account manager|recruit|talent|marketing"
        r"|customer success|support|finance|accounting|legal|counsel|people"
        r"|hr|communications|content|brand|design|designer"
        r"|program manager|project manager|analyst|operations|revenue"
        r"|partnership|business development|bdr|sdr|solutions consultant"
        r"|technical writer|data scientist|research scientist)\b", re.I)),

    ("seniority_too_low", re.compile(
        r"\b(intern|internship|new grad|graduate|junior|jr\.?|associate"
        r"|apprentice|entry.level|co.?op)\b", re.I)),

    ("seniority_too_high", re.compile(
        r"\b(principal|distinguished|fellow|architect|vp|vice president"
        r"|director|head of|chief|cto|(?<!product )manager|management)\b", re.I)),

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
        r"|observability|devops",
        re.I)),

    ("swe", re.compile(
        r"software engineer|backend engineer|back.end engineer|full ?stack"
        r"|swe\b|software developer",
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
        return "rejected", None, "no_family_match"

    if not location_ok(job):
        return "rejected", family, "location"

    if job["comp_min"] is not None and job["comp_min"] < COMP_FLOOR:
        return "rejected", family, "comp_below_floor"

    return "filtered", family, None
