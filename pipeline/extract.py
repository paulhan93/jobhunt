"""JD preprocessing and local-model extraction/matching (step 6, PROJECT.md §7).

The model only reads and extracts (7a) and matches evidence (7b). Judgment is
arithmetic and lives entirely in pipeline/score.py — see PROJECT.md §7 for why.
"""
import html
import json
import re

import httpx
import yaml

# Change this one line to switch extraction/matching from local Ollama to the
# Claude API. Ollama is free and offline; Claude is faster and more accurate
# but costs per call — see PROJECT.md §3 ("local model for volume, cloud
# model for quality"). This is the escape hatch when volume is low enough
# that the tradeoff flips, or when a quality re-run is worth the cost.
PROVIDER = "claude"  # "ollama" | "claude"

# When PROVIDER == "claude" and the run has at least this many jobs, use the
# Batch API (50% cheaper, one submission instead of N round trips) instead of
# a single call per job. Doesn't apply to Ollama — no batch mode exists there,
# and there's no per-call cost to amortize since it's local/free either way.
BATCH_THRESHOLD = 300

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "llama3.2:latest"
CLAUDE_MODEL = "claude-haiku-4-5"

# Controlled vocabulary (PROJECT.md §8). Single Python source of truth — the
# resume.yaml comment block is the human-facing copy; keep both in sync by hand.
SKILL_VOCAB = [
    "python", "java", "typescript", "go", "bash", "sql",
    "ci-cd", "build-systems", "developer-productivity", "test-infrastructure",
    "test-automation", "playwright", "selenium", "observability", "internal-tools",
    "kubernetes", "docker", "terraform", "aws", "gcp", "postgres",
    "api-design", "distributed-systems", "performance", "security", "mentoring",
    "code-review", "llm-integration", "agentic-systems", "evals",
]

# --- JD preprocessing (PROJECT.md §7) -------------------------------------

_TAG = re.compile(r"<[^>]+>")

# ATS content routinely uses typographic quotes/dashes (e.g. "you’ll" not
# "you'll"), which silently breaks straight-quote marker matching below.
_SMART_PUNCT = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-",
})

_COMP_CONTEXT = re.compile(
    r"(salary|compensation|base pay|pay range)[^.]{0,300}", re.I
)
_COMP_RANGE = re.compile(
    r"(?:\$\s?|USD\s+)?(\d{2,3}(?:,\d{3}))(?:\s*USD)?\s*(?:-|–|to|and)\s*"
    r"(?:\$\s?|USD\s+)?(\d{2,3}(?:,\d{3}))(?:\s*(USD|usd))?",
    re.I,
)

_START_MARKERS = [
    "what we're looking for", "what we look for", "what you'll do",
    "what you will do", "what will you do", "requirements", "qualifications",
    "minimum qualifications", "basic qualifications", "about the role", "you have",
    "who you are", "responsibilities",
]
_END_MARKERS = [
    "equal opportunity", "equal opportunity employer", "our culture",
    "what we offer", "benefits", "accommodation is available", "background check",
    "candidate privacy", "the annual base salary", "compensation",
]


def strip_html(raw: str) -> str:
    """Plain text from a raw ATS description (verified: real HTML, not markdown)."""
    text = _TAG.sub(" ", raw or "")
    text = html.unescape(text)
    text = text.translate(_SMART_PUNCT)
    return re.sub(r"\s+", " ", text).strip()


def extract_comp(text: str) -> tuple[float | None, float | None, str | None]:
    """Regex comp extraction over the FULL text, before any section trimming —
    comp is usually in the tail, which extract_relevant_section() discards."""
    window_match = _COMP_CONTEXT.search(text)
    search_space = window_match.group(0) if window_match else text

    m = _COMP_RANGE.search(search_space)
    if not m and window_match:
        m = _COMP_RANGE.search(text)  # fall back to whole text
    if not m:
        return None, None, None

    lo = float(m.group(1).replace(",", ""))
    hi = float(m.group(2).replace(",", ""))
    if lo > hi:
        lo, hi = hi, lo
    # Guard against grabbing something that isn't a plausible annual salary.
    if lo < 20_000 or hi > 1_000_000:
        return None, None, None
    return lo, hi, "USD"


def extract_relevant_section(text: str) -> str:
    """Section-boundary trim (PROJECT.md §7): a JD is a sandwich, not
    back-loaded. Falls back to the whole text if no start marker is found."""
    lower = text.lower()

    start_idx = None
    for marker in _START_MARKERS:
        idx = lower.find(marker)
        if idx != -1 and (start_idx is None or idx < start_idx):
            start_idx = idx
    if start_idx is None:
        return text

    end_idx = None
    for marker in _END_MARKERS:
        idx = lower.find(marker, start_idx)
        if idx != -1 and (end_idx is None or idx < end_idx):
            end_idx = idx

    return text[start_idx:end_idx] if end_idx else text[start_idx:]


# --- Model calls -------------------------------------------------------

def call_ollama(messages: list[dict], schema: dict) -> dict:
    resp = httpx.post(
        OLLAMA_URL,
        json={
            "model": OLLAMA_MODEL,
            "messages": messages,
            "format": schema,
            "options": {"temperature": 0},
            "stream": False,
        },
        timeout=180,
    )
    resp.raise_for_status()
    content = resp.json()["message"]["content"]
    return json.loads(content)


def _extract_text(message) -> str:
    """Pull the text block out of a Claude response. Raises a clear error
    instead of a bare StopIteration when there isn't one — e.g. the safety
    classifier declined (stop_reason == "refusal") and content came back
    empty or non-text. HTTP 200 either way, so this can't be caught upstream
    any other way."""
    for block in message.content:
        if block.type == "text":
            return block.text
    reason = getattr(message, "stop_reason", "unknown")
    raise RuntimeError(f"Claude returned no text content (stop_reason={reason})")


def call_claude(messages: list[dict], schema: dict) -> dict:
    import anthropic  # local import: only needed when PROVIDER == "claude"

    client = anthropic.Anthropic()  # picks up ANTHROPIC_API_KEY / ant profile
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        temperature=0,  # match Ollama path's determinism — without this,
                         # re-running extraction on identical input can
                         # silently produce different requirements/matches.
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=messages,
    )
    return json.loads(_extract_text(response))


def call_model(messages: list[dict], schema: dict) -> dict:
    if PROVIDER == "claude":
        return call_claude(messages, schema)
    return call_ollama(messages, schema)


def _run_claude_batch(requests: list) -> dict[str, dict | None]:
    """Submit a Claude Batch API request, poll to completion, and return
    {custom_id: parsed_json} — None for any request that errored/canceled/
    expired, so callers can tell success from failure per job."""
    import time

    import anthropic

    client = anthropic.Anthropic()
    batch = client.messages.batches.create(requests=requests)

    elapsed = 0
    while batch.processing_status != "ended":
        time.sleep(30)
        elapsed += 30
        batch = client.messages.batches.retrieve(batch.id)
        print(f"  batch {batch.id}: {batch.processing_status} ({elapsed}s elapsed)")

    results: dict[str, dict | None] = {}
    for r in client.messages.batches.results(batch.id):
        if r.result.type == "succeeded":
            try:
                text = _extract_text(r.result.message)
            except RuntimeError:
                # A refusal within a "succeeded" batch result — same failure
                # mode as an errored/canceled/expired request, so treat it
                # the same way: None, not a crash.
                results[r.custom_id] = None
                continue
            results[r.custom_id] = json.loads(text)
        else:
            results[r.custom_id] = None
    return results


_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "kind": {"type": "string", "enum": ["must", "nice"]},
                    "skill_key": {
                        "anyOf": [
                            {"type": "string", "enum": SKILL_VOCAB},
                            {"type": "null"},
                        ],
                    },
                    "years_required": {
                        "anyOf": [{"type": "number"}, {"type": "null"}],
                    },
                },
                "required": ["text", "kind", "skill_key", "years_required"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["requirements"],
    "additionalProperties": False,
}

_EXTRACTION_PROMPT = """You are extracting candidate requirements from a job \
description. Read the job description below and list every distinct \
requirement or qualification a candidate needs — skills, tools, years of \
experience, domain knowledge. Do not extract company perks, benefits, or \
descriptions of the team/product.

For each requirement:
- "text": the requirement in your own concise words (not a generic label).
- "kind": "must" if it's a required/minimum qualification, "nice" if it's \
preferred, a bonus, or a "nice to have".
- "skill_key": pick the single best match from this controlled list if one \
clearly applies, otherwise null. Do not invent a value outside this list: \
{vocab}
- "years_required": the number of years specifically required for THIS \
requirement if the text states one, otherwise null.

Job description:
---
{jd}
---"""


def extract_requirements(jd_text: str) -> list[dict]:
    prompt = _EXTRACTION_PROMPT.format(vocab=", ".join(SKILL_VOCAB), jd=jd_text)
    result = call_model([{"role": "user", "content": prompt}], _EXTRACTION_SCHEMA)
    return result.get("requirements", [])


def extract_requirements_batch(jobs: dict[str, str]) -> dict[str, list[dict] | None]:
    """Claude-only. jobs: {job_id_str: trimmed_jd_text}. One Batch API
    submission for the whole set instead of one call per job."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    requests = [
        Request(
            custom_id=jid,
            params=MessageCreateParamsNonStreaming(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                temperature=0,
                output_config={
                    "format": {"type": "json_schema", "schema": _EXTRACTION_SCHEMA}
                },
                messages=[{
                    "role": "user",
                    "content": _EXTRACTION_PROMPT.format(
                        vocab=", ".join(SKILL_VOCAB), jd=text
                    ),
                }],
            ),
        )
        for jid, text in jobs.items()
    ]
    raw = _run_claude_batch(requests)
    # None means the batch request itself failed (errored/canceled/expired) —
    # must stay distinguishable from a call that succeeded and genuinely found
    # zero requirements. Coalescing both to [] here silently turns a failure
    # into "extracted, nothing found", which downstream scoring can't tell
    # apart from real data and would score as a perfect match.
    return {
        jid: (r.get("requirements", []) if r is not None else None)
        for jid, r in raw.items()
    }


def load_bullet_bank(path: str = "resume.yaml") -> dict[str, str]:
    with open(path) as f:
        data = yaml.safe_load(f)

    bullets = {}
    for role in data.get("experience", []):
        for b in role.get("bullets", []):
            bullets[b["id"]] = b["text"]
    for project in data.get("projects", []):
        for b in project.get("bullets", []):
            bullets[b["id"]] = b["text"]
    return bullets


_MATCHING_PROMPT = """You are checking whether a candidate's resume bullets \
provide evidence for a job's requirements. Only match a bullet to a \
requirement if the bullet text genuinely demonstrates that skill or \
experience — do not force matches. A requirement can have zero matching \
bullets.

Resume bullets (id: text):
{bullets}

Requirements (index: text):
{requirements}

For each requirement index, return the list of bullet ids (from the set \
above, exactly as given) that support it. Use an empty list if none do."""


def _build_matching_schema(bullet_ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "requirement_index": {"type": "integer"},
                        "bullet_ids": {
                            "type": "array",
                            "items": {"type": "string", "enum": bullet_ids},
                        },
                    },
                    "required": ["requirement_index", "bullet_ids"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["matches"],
        "additionalProperties": False,
    }


def match_evidence(
    requirements: list[dict], bullets: dict[str, str]
) -> dict[int, list[str]]:
    """One batched call per job. Returns {requirement_index: [bullet_id, ...]}.
    Bullet ids are enum-constrained in the schema, so the model structurally
    cannot invent evidence (PROJECT.md §7b)."""
    if not requirements or not bullets:
        return {}

    bullets_block = "\n".join(f"{bid}: {text}" for bid, text in bullets.items())
    reqs_block = "\n".join(
        f"{i}: {r['text']}" for i, r in enumerate(requirements)
    )
    prompt = _MATCHING_PROMPT.format(bullets=bullets_block, requirements=reqs_block)

    schema = _build_matching_schema(list(bullets.keys()))
    result = call_model([{"role": "user", "content": prompt}], schema)

    matches = {}
    for m in result.get("matches", []):
        matches[m["requirement_index"]] = m.get("bullet_ids", [])
    return matches


def match_evidence_batch(
    jobs: dict[str, list[dict]], bullets: dict[str, str]
) -> dict[str, dict[int, list[str]] | None]:
    """Claude-only. jobs: {job_id_str: requirements_list}. Skips jobs with no
    requirements (nothing to match), same as match_evidence's early return."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    if not bullets:
        return {}

    bullets_block = "\n".join(f"{bid}: {text}" for bid, text in bullets.items())
    schema = _build_matching_schema(list(bullets.keys()))

    requests = []
    for jid, reqs in jobs.items():
        if not reqs:
            continue
        reqs_block = "\n".join(f"{i}: {r['text']}" for i, r in enumerate(reqs))
        prompt = _MATCHING_PROMPT.format(bullets=bullets_block, requirements=reqs_block)
        requests.append(Request(
            custom_id=jid,
            params=MessageCreateParamsNonStreaming(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                temperature=0,
                output_config={"format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": prompt}],
            ),
        ))

    if not requests:
        return {}

    raw = _run_claude_batch(requests)
    # Same reasoning as extract_requirements_batch: None means the request
    # failed and must stay distinguishable from "succeeded, zero matches".
    out: dict[str, dict[int, list[str]] | None] = {}
    for jid, r in raw.items():
        if r is None:
            out[jid] = None
            continue
        matches = {}
        for m in r.get("matches", []):
            matches[m["requirement_index"]] = m.get("bullet_ids", [])
        out[jid] = matches
    return out
