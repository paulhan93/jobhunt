# Job Hunt Pipeline

A local-first pipeline that ingests job postings from public ATS APIs, filters them
against my criteria, scores fit against a structured resume bank, and surfaces a
ranked review queue. Submission stays manual by design.

This document is the source of truth for architecture and conventions. Read it
before changing the schema, adding a data source, or adding a pipeline stage.

**Status: steps 1–6 complete.** 70 companies, ~7,660 postings, filter reduces to
252 reviewable. `resume.yaml` is written; extraction (local model → `requirements`)
and arithmetic scoring are built, tested against real bugs, and mid-run clearing
the backlog (229 extracted, 5 scored, 0 errors as of 2026-08-16). A second,
optional Claude-API path exists alongside the default local one — see §7a.
**Per §11: stop building, spend a week applying to what's already scored.**

---

## 1. Purpose and framing

### The problem

Cold applications through job portals are the lowest-yield channel in a job
search — referrals and warm intros convert far better. But cold applications
still need to happen, and done manually they consume 5–10 hours a week: finding
postings, reading JDs, judging fit, tailoring a resume, tracking what was sent
where.

This project automates that low-yield channel *because* it's low-yield. The goal
is not to make cold applications convert better. It's to reduce their time cost
to near zero so that time can go to referrals, networking, and interview prep.

### What is and isn't automated

| Stage | Automated | Why |
|---|---|---|
| Finding postings | Yes | Pure mechanical polling |
| Filtering junk | Yes | Deterministic rules |
| Extracting requirements | Yes | Reading comprehension, low stakes |
| Scoring fit | Yes | Arithmetic over extracted data |
| Review / decision | **No** | Judgment; the point of the pipeline |
| Resume tailoring | Assisted | LLM drafts, human approves |
| Submission | **No** | See below |

**Submission is deliberately manual.** Application forms are per-company, have
knockout questions and custom fields, and break constantly. More importantly,
mass-submitting generated applications is a pattern recruiting teams detect, and
being flagged as a bulk applicant at a company you actually wanted is a
permanent cost. The 90% time win is in triage and drafting; the last mile is
cheap to do by hand and expensive to get wrong.

### Non-goals

- **No LinkedIn or Indeed scraping.** ToS violation, strong anti-bot, account
  risk. Everything here uses documented public endpoints that companies' own
  careers pages call.
- **No auto-submit.** See above.
- **No distributed architecture.** One person, ~7,200 postings, ~200 new/day.
  SQLite and cron are correct. No Redis, Celery, Postgres, or Kafka.
- **No web app.** Datasette over the SQLite file is the review UI until it
  demonstrably isn't enough.

---

## 2. Personal context

This drives filter design and scoring, so it belongs in the doc.

- **Name:** Paul. Based in Portland, Oregon.
- **Current role:** Software Engineer in Test (SDET), mid-level.
- **Target:** Senior, with a strong preference for **Developer Productivity /
  Platform / Test Infrastructure** work.
- **Why that direction:** University capstone was a bottleneck-reduction project
  at an internship; current role is adjacent to it; genuinely enjoys owning a
  product that measurably improves other engineers' productivity. Three
  independent signals pointing the same way.

### Positioning narrative

Not "I'm in test but want to be a real engineer" (defensive, concedes the
premise). Instead:

> "I build the systems that make other engineers faster. I started in test
> infrastructure because that's where the biggest bottleneck was."

The capstone is the origin point that makes this a trajectory rather than a
pivot. It earns a resume line despite being older.

### Role families, in priority order

1. `platform` — DevProd, DevEx, Developer Productivity, Engineering
   Productivity, Build/Release Engineering, Test Infrastructure, Test Platform,
   Internal Tools, Developer Platform, Tooling
2. `sdet` — Senior SDET, Software Engineer in Test, Test Automation, QA Engineer.
   Priority mainly because of existing experience here, not because it's the
   preferred long-term track.
3. `swe` — general product/backend engineering (wide net, lower conversion)
4. `ai_eng` — AI Engineer, Applied AI Engineer, Agentic *, AI Automation
   Engineer, LLM Engineer. Added 2026-08-16: directly matches demonstrated
   project experience (`privew`'s multi-agent pipeline, this pipeline's own
   model-does-extraction/arithmetic-does-judgment design) and the stated
   preference for system design over hands-on coding. Deliberately excludes
   generic "Machine Learning Engineer" — that title usually means classic
   ML/data-science work, a different skillset than agentic/orchestration work.
5. `tpm` — **Technical** Product Manager specifically (title says "Technical
   Product Manager", or "Product Manager" combined with a technical/platform
   context word in the title). Low priority. Generalist Product Manager
   remains explicitly out of scope (see note below) — this family exists only
   for the dev-tools/platform-adjacent flavor of PM.
6. `customer_eng` — Solutions Engineer, Sales Engineer, Forward Deployed
   Engineer, Customer Engineer, Developer Advocate. Real engineering, but
   customer-facing. Secondary track kept visible rather than rejected.
7. `sre` — SRE, Infrastructure Engineer, Observability, DevOps. Heavy overlap
   with platform and good comp, but explicitly lower personal interest than
   the families above it — don't over-index on volume here.

Generalist Product Manager (roadmap/business ownership with no particular
technical bent — growth PM, consumer PM, etc.) stays out of scope: different
job function than everything else in this pipeline, no overlap with
`resume.yaml`'s bullet bank, and comp doesn't clear senior SWE except at big
tech. Technical Product Manager (`tpm` above) was kept in scope on a follow-up
(2026-08-16) — the "decide what to build for a developer-facing system" niche
is a real, if lower-priority, fit given the platform/DevProd focus elsewhere in
this list.

Solutions Architect and generalist PM/Technical Program Manager were also
discussed as good longer-term fits given a stated preference for system design
over hands-on coding, but are intentionally not pipeline families right now —
Solutions Architect is a later-career title, not a near-term target.

Review tiers 1–3 exhaustively/regularly, tiers 4–5 selectively, tiers 6–7
mainly when the queue is thin.

### Notes for scoring

- "Aspiring senior" means **include both mid and senior postings.** Do not
  exclude `Software Engineer II/III`.
- Exclude `Staff` and above, but as its own reason (`seniority_staff`) so the
  bucket stays reviewable — Staff at a 60-person startup means something
  different from Staff at Databricks.
- **Critical exclude:** `Manual QA`, `QA Analyst`, `QA Tester`, `Game Tester`,
  `Performance Tester`, `Localization QA`. These share keyword space with target
  roles but are a step backward in comp and scope.
- Senior JDs routinely ask for 5–8 years. Expect many `stretch` verdicts from
  the deterministic years check. Those requirements are wishlists; apply anyway
  when the gap list is short.

---

## 3. Architecture

```
data/companies.txt  (hand-curated, ~100 names)
        |
        v
   probe.py  ──────────>  data/probe_results.json   (checkpoint: slow + flaky)
        |
        v
 load_companies.py  ───>  companies table            (71 resolved)
        |
        v
   fetch.py  (cron, every 6h)  ───>  jobs table      (~7,200 rows)
        |                            raw_json, first_seen_at, closed_at, status
        v
  filter.py  (rules only, no LLM)  ──> ~97% rejected, role_family tagged
        |
        v
  extract.py  (local model via Ollama)  ──> requirements table
        |
        v
  score.py  (pure arithmetic, no model)  ──> jobs.fit_score
        |
        v
  REVIEW  (datasette — human decision)
        |
        v
  tailor.py  (cloud model)  ──> Typst  ──> PDF
        |
        v
  APPLY  (manual)  ──> applications table
        |
        v
  report.py  (weekly aggregate across all requirements)
```

### Design decisions worth preserving

**Collecting is decoupled from processing.** Fetch writes to a durable queue;
processing reads from it. Slow LLM calls can't block collection, a crash in
scoring doesn't lose jobs, and the entire corpus can be re-scored with a better
prompt without re-fetching anything.

**The queue is SQLite, not a queue system.** A `status` column plus a partial
index is a perfectly good work queue at this volume. There is no separate queue:
`filtered` means awaiting extraction, `extracted` awaiting scoring, `scored`
awaiting human review.

**Polling, not streaming.** Job postings change slowly. Cron every 6h is
correct and cheaper than anything live.

**Checkpoint after the slow, unreliable stage.** `probe.py` writes to JSON
before anything touches the database.

**Cheap filters run before expensive stages.** Deterministic rules reject ~97%
of rows for free. Only survivors reach a model.

**Normalize at the boundary.** Each ATS's response shape is absorbed by one thin
translation layer (`pipeline/ats.py`, `pipeline/fetch.py`). Never let
`if source == "lever"` leak into filter or scoring code.

**Local model for volume, cloud model for quality.** Local (Ollama) handles
extraction and matching by default. A frontier API handles the few resumes
actually sent (step 7). `pipeline/extract.py` also carries a `PROVIDER =
"ollama" | "claude"` escape hatch (one line) for a one-time quality re-run or
if volume ever drops low enough that per-call cost stops mattering — Claude
(Haiku) is measurably more accurate on the same prompts, not just faster. Above
`BATCH_THRESHOLD` (300) it auto-switches to the Batch API, deliberately set
above this project's realistic scale — see §7a.

**Model does extraction; arithmetic does judgment.** See §7.

---

## 4. Data model

Full schema lives in `schema.sql`. **Changes are now migrations, not resets** —
see §9.

```sql
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS companies (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    ats        TEXT NOT NULL CHECK (ats IN (
                   'greenhouse','lever','ashby','smartrecruiters','workable')),
    slug       TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    notes      TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (ats, slug)
);

CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER REFERENCES companies(id),
    source        TEXT NOT NULL,
    source_job_id TEXT NOT NULL,

    title         TEXT NOT NULL,
    location      TEXT,
    remote        INTEGER,
    description   TEXT,
    apply_url     TEXT,
    comp_min      REAL,
    comp_max      REAL,
    comp_currency TEXT,

    raw_json      TEXT NOT NULL,

    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at  TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at     TEXT,

    status        TEXT NOT NULL DEFAULT 'new' CHECK (status IN (
                      'new','filtered','extracted','scored',
                      'reviewed','applied','rejected','error')),
    role_family   TEXT CHECK (role_family IN (
                      'swe','sdet','platform','sre','customer_eng','tpm','ai_eng')),
    reject_reason TEXT,
    fit_score     REAL,
    fit_tier      TEXT CHECK (fit_tier IN ('apply','stretch','skip')),
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,

    UNIQUE (source, source_job_id)
);

CREATE TABLE IF NOT EXISTS requirements (
    id              INTEGER PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    text            TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('must','nice')),
    skill_key       TEXT,
    years_required  REAL,
    matched_bullets TEXT          -- JSON array of bullet IDs
);

CREATE TABLE IF NOT EXISTS applications (
    id             INTEGER PRIMARY KEY,
    job_id         INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
    applied_at     TEXT NOT NULL DEFAULT (datetime('now')),
    resume_version TEXT,
    referral       TEXT,
    heard_back     INTEGER NOT NULL DEFAULT 0,
    outcome        TEXT,
    notes          TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_queue
    ON jobs(status) WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_req_job      ON requirements(job_id);
CREATE INDEX IF NOT EXISTS idx_req_skill    ON requirements(skill_key);
```

### Schema rationale

- **`UNIQUE (source, source_job_id)`** is the entire dedupe strategy. With
  `ON CONFLICT ... DO UPDATE` the fetcher is **idempotent** — verified: a second
  consecutive run reported 1 new out of 7,182. Protect this property.
- **`raw_json NOT NULL`** so a fetcher can never forget it. Postings close and
  vanish from boards; the raw response is the only way to reprocess history.
- **`company_id` is nullable** on purpose, to leave room for sources with no
  curated company row (USAJOBS, HN).
- **Three timestamps.** `first_seen_at` is the only trustworthy freshness signal
  (see §5). `last_seen_at` powers closed detection. `closed_at` is a nullable
  timestamp rather than a boolean — answers "is it" and "when" for free.
- **`status` is a state machine** with a `CHECK`. A typo'd status is a job that
  silently vanishes from the pipeline with no error.
- **`attempts` / `last_error`** because extraction will crash on some JD. Worker
  takes `WHERE attempts < 3`, increments on entry, records the exception.
- **`fit_tier`** (migration 003) is the human-facing verdict derived from
  `fit_score`: `apply` (≥70), `stretch` (≥40, or any must-have capped by the
  years check), `skip` (below). Thresholds are a starting guess, tunable like
  the filter rules once real scores are in — not derived from data yet.
- **`matched_bullets` as JSON** is a deliberate compromise. The pure form is a
  join table, but this array is only ever read whole for one requirement.
- **`ON DELETE CASCADE`** on requirements, deliberately **not** on applications
  (a bug deleting a job should fail loudly, not erase application history).

### What is not in the database

The resume bullet bank lives in `resume.yaml`. Heuristic: **if a human edits it
and the program only reads it, it's a file.**

---

## 5. ATS reference

All endpoints are public, unauthenticated GETs — the same ones companies' own
careers pages call.

| ATS | Endpoint |
|---|---|
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs` (+`?content=true`) |
| Lever | `https://api.lever.co/v0/postings/{slug}?mode=json` |
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true` |
| SmartRecruiters | `https://api.smartrecruiters.com/v1/companies/{slug}/postings` |
| Workable | `https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true` |

### Quirks — every one of these has caused a real bug

**Response shape differs.** Lever returns a bare array. SmartRecruiters names its
list `content`. Greenhouse, Ashby, and Workable use `jobs`. Absorbed by
`count_jobs()` in `pipeline/ats.py`.

**HTTP 200 does not mean the request was valid.** Greenhouse, Lever, and Ashby
404 on an unknown slug. **Workable and SmartRecruiters return 200 with an empty
result set**, which made the probe assign `workable/microsoft` and
`smartrecruiters/google` — 38 false positives. Hence
`STRICT_404 = {greenhouse, lever, ashby}`.

**Required query params are easy to miss silently.** Greenhouse returns empty
descriptions without `?content=true`. Ashby returns no compensation without
`?includeCompensation=true` — this cost all 1,310 Ashby rows their salary data
until caught.

**Field types are not what the name suggests.** SmartRecruiters returns `ref` as
a plain URL string, not an object — crashed two companies. Ashby compensation
components can be typed `Salary` with `minValue: null`, and `interval`
distinguishes `1 YEAR` from hourly (an unguarded parser would store `85` in a
field compared against an annual floor).

**The ATS `remote` flag is unreliable.** Observed `remote = 1` on postings whose
location is bare `San Francisco`. Never treat it as sufficient on its own.

**Location is often a list, not a value.** Real example: `"San Francisco, CA,
New York, NY, Portland, OR, or Remote within Canada or United States"`. Filters
must look for an acceptable option *anywhere* in the string rather than matching
the whole field, and must not reject on a foreign country that appears alongside
a US option.

**Pagination is five different ideas.** Greenhouse and Ashby return whole boards.
SmartRecruiters uses `limit`/`offset` and caps at 100 (Canva and Wise both
returned exactly 100 — a page cap, not a coincidence). Workable's newer endpoint
uses an opaque continuation token.

**Dates lie in different ways.** Lever gives `createdAt` in epoch milliseconds.
Greenhouse's `updated_at` changes on any edit. Workday gives `postedOn` as
English text. **Never trust source timestamps.**

**Exclude descriptions from any change fingerprint.** ATS platforms rewrite
whitespace and tracking params constantly; including them flags every job as
updated every day.

**Slug discovery is the real problem.** No master list, no search endpoint.
Generic generation resolves ~65%; the rest are arbitrary
(`Harness → harnessinc`, `Sonar → sonarsource`, `Nx → nrwl`) and cheaper to
resolve by hand.

**Politeness.** ~0.4s between requests, real email in the User-Agent, retry with
backoff on 429/5xx, 30s timeout.

### Measured coverage

- **71 companies originally resolved**, now **70 active** (the Uber
  SmartRecruiters row was deleted, decision 15 — 1 posting on a stale/squatted
  account, not Uber's real board): greenhouse 40, ashby 24, lever 4 (+2
  SmartRecruiters deactivated — their list endpoint has no descriptions).
- **~7,660 postings** as of 2026-08-16 (grows via cron), descriptions 100%
  populated for greenhouse/ashby/lever.
- **Compensation:** Ashby 622/1,310. Greenhouse and Lever expose none — but many
  descriptions state ranges in text, recoverable by regex with no re-fetch.
- **15 companies unresolvable** (Workday/homegrown): Google, Microsoft, Amazon,
  Apple, Meta, Netflix, Nvidia, GitHub, Shopify, Atlassian, Slack, DoorDash,
  Intel, Nike, AWS Elemental. Marked `no_public_ats` so the probe skips them.
- **Boards returning 0 jobs from a valid endpoint:** Deel, Plaid, Snyk,
  Expensify. Probably moved boards.

### Workday (not yet implemented)

No documented public API, but every Workday careers site is an SPA calling:

```
POST https://{tenant}.wd5.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
{"limit": 20, "offset": 0, "searchText": ""}
```

Per-tenant and fiddly, but unlocks a large block of high-value targets at once.

---

## 6. Filtering (step 5, complete)

Rules only — no model. Runs on `status = 'new' AND closed_at IS NULL`. Sets
`filtered` or `rejected` with a reason, and tags `role_family`.

`scripts/filter_all.py --reset` returns `filtered`/`rejected` rows to `new` so
rules can be retuned against real data. It deliberately never touches `reviewed`
or `applied`.

### Measured results

As of 2026-08-16, ~7,180 classified → **252 passing** (numbers drift as cron adds
new postings and rules get retuned — see below):

| reason | n |
|---|---|
| not_engineering | 2,790 |
| seniority_too_high | 1,520 |
| no_family_match | 1,100 |
| location | 924 |
| seniority_staff | 511 |
| seniority_too_low | 79 |
| **PASSED** | **252** |
| comp_below_floor | 4 |
| manual_qa | 1 |

By family: `swe` 150, `customer_eng` 72, `sre` 20, `platform` 6, `sdet` 4.

**Fixed a real bug in `not_engineering`:** a bare `sales` keyword was rejecting
every "Sales Engineer" title before it ever reached `_FAMILIES`, even though
`customer_eng`'s pattern explicitly lists `sales engineer` — dead code, because
the reject ran first. Removing the bare keyword (and trusting `_FAMILIES`'
allowlist to still catch genuine non-engineering `sales` titles like "Sales
Manager") moved 61 rows out of the black hole; 10 now correctly pass as
`customer_eng`. `customer_eng` jumped 56 → 72 as a result — most of that swing
is this fix, not organic growth. Same fix pattern applied for `product manager`
when `tpm` was added as a family (§2) — `(?<!product )manager` in
`seniority_too_high` so "Product Manager" doesn't collide with the bare
`manager` reject.

**A known, not-yet-fixed instance of the same bug class:** bare `design` in
`not_engineering` catches "Frontend Engineer - Design Systems" (an engineering
title) the same way `sales` caught Sales Engineer. Left alone deliberately —
flagged, not fixed, pending a decision on how many more of these exist.

### What the numbers mean

**Tier 1 is small.** 6 platform + 4 sdet = 10 roles across 70 companies,
refreshing by a few per week. `sre` (19 more) is available as a larger pool if
the top tiers run dry, but it's a lower-interest fallback, not a target to
chase for volume. The real lever is **the company list** — 30 more large
engineering orgs would move the platform/sdet number more than any filter
tuning.

**The niche is geographically concentrated.** 8 US/Canada DevProd roles were
rejected on location alone (SF, NYC, Toronto, Foster City) versus 7 that passed.
The niche exists; it clusters in metros. Those rows keep `role_family='platform'`
with `reject_reason='location'`, so one query surfaces them if the queue ever
feels thin — a deliberate design choice over discarding them.

**Bias toward passing when evidence is absent.** The comp rule only fires when
`comp_min IS NOT NULL` (9% coverage). A false reject is invisible; a false pass
costs ten seconds of review. Asymmetric costs, so default to passing.

**Duplicates are real.** ClickHouse posted "QA Engineer - Core Database" four
times with different `source_job_id` values (one per location). Correct behavior
for `UNIQUE (source, source_job_id)`, but it inflates the queue. A soft dedupe on
`(company_id, title)` at review time would collapse them.

### Ordering matters in two places

`_REJECTS` runs before family matching — no point classifying something being
discarded. And `_FAMILIES` is ordered, first match wins, with `customer_eng`
**first**: customer-facing is a stronger signal than any product-area word.
Without that, "Senior Customer Engineer — Developer Platform" tags as `platform`.

`Platform` and `Infrastructure` are the most overloaded words in engineering
titles. "Data Platform," "AI Platform," "Growth Platform" are product teams, not
developer productivity. The distinction that matters: **is the customer an
engineer at this company, or a user of the product?**

---

## 7. Fit scoring (step 6, complete)

The model does **extraction only**. Judgment is arithmetic. This is the most
important design decision in the project.

Asking an LLM to rate anything 0–100 returns 72–85 for nearly everything — a
number that feels precise and means almost nothing. Instead:

**7a. Extract requirements** (local model, schema-constrained via Ollama's
`format` param). One row per requirement into `requirements`:
`{text, kind: must|nice, skill_key, years_required}`. Pure reading comprehension
— no judgment about the candidate.

`skill_key` must be normalized against the controlled vocabulary in §8.
Free-text skill names will wreck the aggregate report.

**7b. Match evidence** (local model, per requirement, constrained to valid bullet
IDs from `resume.yaml`). Returns supporting bullet IDs or empty. Output
constrained to an existing ID set means the model *structurally cannot* invent
evidence.

**7c. Score** — no model involved:

```
must_hit = matched must-haves / total must-haves
nice_hit = matched nice-to-haves / total nice-to-haves
fit      = 100 * (0.75 * must_hit + 0.25 * nice_hit)
```

Years is fully deterministic: sum date ranges of roles tagged with that
`skill_key`, compare to `years_required`. If any must-have fails on years by more
than ~2x, cap the recommendation at `stretch` regardless of score.

Tiers: `apply` / `stretch` / `skip`.

**Why this matters more than the number:** every point traces to a
requirement → bullet edge. When a score looks wrong you can see exactly which
requirement went unmatched and whether the model missed evidence that exists.
The gap lists are the genuinely useful output.

### JD preprocessing is required first

Real finding from reading actual descriptions: **a JD is a sandwich, not
back-loaded.** Measured on a real 1Password posting:

- 0–1,900 chars: company boilerplate (ARR, awards, mission, culture)
- 1,900–5,600: the actual content (role summary, requirements, responsibilities)
- 5,600–end: comp, culture, AI policy, remote policy, benefits, EEO, background
  check, AI-screening opt-out — roughly 40% pure noise

Positional truncation was considered and **rejected** — keeping the last 4,000
characters would cut the requirements section in half and keep the EEO statement.

Use **section-boundary detection**:

- *Start markers:* `What we're looking for`, `What you'll do`, `Requirements`,
  `Qualifications`, `Minimum qualifications`, `Basic qualifications`,
  `About the role`, `You have`, `Who you are`, `Responsibilities`
- *End markers:* `Equal Opportunity`, `equal opportunity employer`,
  `Our culture`, `What we offer`, `Benefits`, `Accommodation is available`,
  `background check`, `Candidate Privacy`, `The annual base salary`,
  `Compensation`

Keep from the first start marker to the first following end marker; fall back to
the whole description if nothing matches. **Extract comp from the tail with a
regex before discarding it** — that 1Password JD stated `$113,000 USD and
$158,000 USD` in plain text, which is the fix for zero Greenhouse comp coverage.

Caveat: `What you can expect` means responsibilities at some companies and
benefits at others. Ambiguous markers need the content checked, not the label
trusted.

### Real bugs found building this (not theoretical)

**Typographic quotes silently broke section detection.** Marker matching used
straight quotes (`what you'll do`); real JDs routinely use curly ones
(`what you’ll do`, U+2019). With no marker match, the code fell through to an
incidental mid-sentence use of a reject word, producing a ~60-character garbage
slice instead of the real section — two unrelated jobs both collapsed to this
and got identical, generic model output as a result. Fixed by normalizing
smart quotes/dashes to ASCII before matching, and by broadening the marker list
for real-world phrasing (`what we look for`, `what will you do`). Checked
against all 247 filtered jobs afterward; one further miss found and fixed the
same way.

**The comp regex missed its own motivating example.** The 1Password JD quoted
above (§7, "extract comp from the tail") states pay as `"$113,000 USD and
$158,000 USD"` — `USD` after *each* number, not just the range's end. The
first regex only handled `USD` at the end (`"$X - $Y USD"`) and silently
returned no match on the exact pattern that justified building it. Fixed;
re-verified against the live 1Password posting in the DB.

## 7a. Provider choice: Ollama default, Claude escape hatch

`pipeline/extract.py` has `PROVIDER = "ollama" | "claude"` (one line) and
`BATCH_THRESHOLD = 300`. Ollama stays the default per §3/§7's local-for-volume
stance; Claude (`claude-haiku-4-5`) is a deliberate escape hatch, not a
migration — for a one-time quality re-run, or if per-call cost stops mattering.
Runs under the threshold call Claude once per job; at or above it, one Batch
API submission handles the whole set (50% cheaper, no per-job round trip) — but
`BATCH_THRESHOLD` is set well above this project's realistic volume on purpose:
at this project's scale (a few hundred jobs at most) the Batch API's cost
saving is a few dollars at most, while its latency floor (~90s minimum observed
on a 2-request test batch, no guaranteed turnaround beyond "usually under an
hour") can be *slower* than just making the sequential calls. Batch only wins
at genuinely large volume, which isn't this project's steady state.

**Schema portability gotcha, found by testing before it shipped:** the same
JSON schema that works on Ollama isn't valid on Claude's structured output.
Claude requires `additionalProperties: false` explicit on every object level,
and rejects `"type": ["string", "null"]` combined with an `enum` containing
`null` — Ollama tolerates both. Fixed by adding `additionalProperties: false`
everywhere and switching nullable enum fields to `anyOf: [{type, enum}, {type:
"null"}]`. Re-verified both providers still produce correct output after the
fix — this is why a schema meant to serve both providers needs testing against
both, not just one.

---

## 8. Resume bank (`resume.yaml`)

Two categories of content, which behave differently and shouldn't be mixed:

**Passthrough** — rendered verbatim, never model-touched: name, contact, links.
**Selectable** — chosen from, tagged, matched: bullets, skills, projects,
summaries. These need IDs and tags.

```yaml
contact:
  name: Paul Han
  email: you@example.com
  phone: "+1 503 555 0134"
  location: Portland, OR
  linkedin: linkedin.com/in/handle
  github: github.com/handle

preferences:            # criteria, consumed by the filter
  remote_ok: true
  onsite_metros: [Portland OR]
  comp_floor: 140000
  open_to_relocation: false

summaries:              # tailoring picks one by role_family
  - id: sum-platform
    for: platform
    text: "..."

skills:
  - id: skill-ci
    label: CI/CD & Build
    items: [GitHub Actions, Jenkins, Gradle, Bazel]
    tags: [ci-cd, build-systems]

experience:
  - company: Acme Corp
    title: Software Engineer in Test
    location: Portland, OR
    start: 2022-03
    end: null           # null = present; needed by the years calculation
    bullets:
      - id: acme-1
        text: "Cut CI feedback time from 40 to 9 minutes across 60 engineers by
                introducing test sharding and a remote build cache."
        tags: [ci-cd, build-systems, developer-productivity, python]

projects:
  - id: proj-capstone
    name: Build Pipeline Bottleneck Reduction (Senior Capstone)
    year: 2021
    bullets:
      - id: cap-1
        text: "..."
        tags: [build-systems, developer-productivity, performance]

education:
  - school: University
    degree: BS Computer Science
    year: 2021
```

### Rules

**~8 bullets per role, not 4.** The bank is deliberately 3–4× larger than any
resume sent, because tailoring selects from it. A thin bank means nothing to
select.

**Skills need tags too.** They're weaker evidence than bullets — a bullet
demonstrates use, a skill claims it — but an untagged skill can't match a
requirement at all.

**Summary variants keyed by `role_family`.** The one free-form field the model
writes; a per-family starting point means the platform version leads with
bottleneck removal.

### Controlled vocabulary for tags and `skill_key`

Keep this list in a comment at the top of `resume.yaml`. Drift here breaks the
step-9 aggregate report.

```
python java typescript go bash sql
ci-cd build-systems developer-productivity test-infrastructure test-automation
playwright selenium observability internal-tools
kubernetes docker terraform aws gcp postgres
api-design distributed-systems performance security mentoring code-review
llm-integration agentic-systems evals
```

`llm-integration`, `agentic-systems`, `evals` added 2026-08-16 for the `ai_eng`
family — before this, the vocabulary had zero coverage for LLM/agent work, so
extraction had no `skill_key` to assign an "AI Engineer" JD's actual
requirements and the step-9 demand report couldn't see this category at all.

### SDET → platform translation (the highest-leverage part)

Write every bullet in **engineering** terms, not QA terms. This determines
whether the matcher finds evidence for platform requirements.

- Weak: "Wrote automated tests for the checkout flow using Playwright."
- Strong: "Built a Playwright framework covering 40 checkout paths; cut release
  regression from 2 days to 3 hours."

Same work; the second matches `python`, `ci-cd`, and `developer-productivity`.
Quantify in **time saved for other engineers**, not coverage percentages.
"Increased test coverage to 85%" is a QA metric; "cut CI feedback time from 40 to
9 minutes across 60 engineers" describes the same competence in platform terms.

Surface general engineering work that isn't on the current resume: internal
tools, CI pipeline changes, mock services, flakiness dashboards, test harnesses
that are real distributed systems. SDETs almost always have more of this than
their resume shows — and it's exactly what matches DevProd requirements.

### Tailoring (step 7)

The model returns **bullet IDs**, validated against the known set:

```python
class TailoredResume(BaseModel):
    summary: str                    # only free-form field
    selected_bullets: list[str]     # validated against BANK_IDS
    skills_order: list[str]
    reword: dict[str, str] = {}     # id -> tightened phrasing, same claim
```

Any reworded bullet is diffed against the original before it ships. Never let a
model regenerate employment history from prose — it will invent a job.

Render with Typst, not LaTeX. Single column, standard headings, real selectable
text, no tables or graphics. The "ATS auto-rejects on formatting" idea is largely
myth, but recruiters *do* run keyword searches inside the ATS.

---

## 9. Layout and conventions

```
jobhunt/
├── PROJECT.md              this file
├── README.md               orientation for a cold reader
├── DECISIONS.md            architecture decision records
├── .gitignore              .venv/ *.db __pycache__/ + personal data
├── schema.sql              source of truth for a FRESH database
├── migrations/             numbered record of changes to LIVE databases
├── reset.sh                drop + recreate from schema.sql (early-stage only)
├── requirements.txt
├── resume.yaml             bullet bank — gitignored (personal)
├── data/
│   ├── companies.txt        hand-curated, # comments supported — gitignored
│   ├── companies.example.txt
│   └── probe_results.json   checkpoint from probe.py — gitignored
├── pipeline/               importable library
│   ├── db.py               get_conn() — timeout=30, several scripts hold jobs.db at once
│   ├── ats.py              endpoints, PROBE_ORDER, STRICT_404, count_jobs, slug_candidates
│   ├── models.py           NormalizedJob
│   ├── fetch.py            per-ATS parsers → NormalizedJob
│   ├── filters.py          rules + role_family tagging
│   ├── extract.py          JD preprocessing + model calls (§7, §7a) — PROVIDER switch here
│   └── score.py            arithmetic (§7c) — fit_score, fit_tier
└── scripts/                entry points
    ├── probe.py
    ├── fix_slugs.py
    ├── load_companies.py
    ├── fetch_all.py
    ├── filter_all.py
    ├── extract_all.py      per-job loop, or process_batch() over BATCH_THRESHOLD
    ├── score_all.py
    └── report.py           (step 9, not yet built)
```

`pipeline/` is imported, `scripts/` is executed. Scripts stay thin.

### Conventions

- **Run scripts as modules from the project root:** `python -m scripts.probe`.
  Not `python scripts/probe.py` — that puts `scripts/` on the import path and
  `from pipeline.db import ...` fails.
- **Syntax-check after every edit:** `python -c "import pipeline.filters"`. One
  second, versus finding an `IndentationError` four minutes into a fetch.
- **Always use `pipeline.db.get_conn()`**, never `sqlite3.connect()` directly.
  `PRAGMA foreign_keys` is **per-connection and defaults OFF** in SQLite — miss
  it and every `REFERENCES` / `ON DELETE CASCADE` is decoration that silently
  permits orphaned rows. `get_conn()` also sets `row_factory = sqlite3.Row`.
- **`with get_conn() as conn:`** commits on success, rolls back on exception.
- **Schema changes are two edits now:** the `ALTER TABLE` for the live database
  (recorded in `migrations/`) *and* the corresponding line in `schema.sql` for
  future ones. Do only the first and the file rots; only the second and the
  current DB breaks. Both halves, every time.
- **Every stage must be idempotent and resumable.** Write results incrementally,
  skip already-done work on entry. Rerunning must be free. Caught in practice:
  `extract_all.py`'s first draft wrapped an entire multi-hour run in one
  `with get_conn() as conn:` block, meaning nothing committed until the whole
  run finished — a crash near job 200 of 252 would have rolled back everything
  already done. Fixed to commit after every job.
- **Network functions return verdicts, never raise.** When loop iterations are
  independent, failure is a value the loop handles, not an exception that ends a
  71-company run.
- **Ambiguous outcomes get their own category.** `hit`/`empty`/`miss`/
  `no_public_ats`; `filtered`/`rejected` with a *reason*. Never a forced binary.
- **Never assume the shape or type of a response you didn't create.** Curl the
  real endpoint before writing a parser for it.
- **Bias toward passing when evidence is absent.** False rejects are invisible;
  false passes cost seconds.

---

## 10. Build plan

### Done

**Step 1 — Schema.** `schema.sql`, `reset.sh`, `pipeline/db.py`.

**Step 2 — Company list and ATS probe.** 71 of 101 companies resolved.

**Step 3 — Fetchers.** Five per-ATS parsers → `NormalizedJob`. Idempotency
verified (second run: 1 new of 7,182). ~7,200 postings.

**Step 4 — Closed detection + retry.** Absence-based closed detection inside the
fetch loop, guarded against truncated responses (skip if fetched count < 70% of
open count). Retry with backoff on timeouts and 5xx. **Confirmed working** — a
later cycle correctly closed 10 jobs across Block, Coinbase, Lyft, Reddit,
Stripe, Vercel.

**Step 5 — Filter.** 7,186 → 252 (numbers drift with cron and rule fixes), 
role_family tagged. See §6.

**Step 4b — cron.** `0 */6 * * *`, output to `logs/`. Installed and running —
confirmed working across multiple cycles (new postings picked up, closed jobs
detected).

**Step 6a — `resume.yaml`.** Written. See §8.

**Step 6b — JD preprocessing.** Section-boundary extraction + comp regex. See
§7's "Real bugs found building this" — both needed real fixes after testing
against live JDs, not just the design as originally specced.

**Step 6c — Extraction + scoring.** `scripts/extract_all.py` /
`scripts/score_all.py` built, tested against real API responses on both
providers, and running against the 252-job backlog (229 extracted, 5 scored, 0
errors as of 2026-08-16 — should finish clearing shortly). See §7a for the
Ollama/Claude provider switch built alongside this.
*Done when:* `datasette serve jobs.db`, sort by `fit_score` desc, and the top 10
are jobs worth considering — pending the backlog finishing and a real look at
the results.

**Steps 1–6 are the MVP — complete.** Per §11: **stop building, spend a week
applying** to what's already scored before touching step 7. The queue doesn't
need to be perfect to be useful.

### Next

**Apply.** Review the scored queue (tier 1/2 first), spend two minutes per job
checking for a referral, and submit by hand. This is the actual next action,
not more code.

**Re-run `filter_all.py`** — 482 jobs are sitting in `status='new'`, unfiltered
since the `tpm`/`ai_eng` families and the `sales`/`design`-class keyword fixes
landed. Some of that backlog may now pass that didn't before.

### Then (after a week of applying, not before)

**Step 7 — Tailoring.** Bullet-ID selection → Typst → PDF.

**Step 8 — Outcomes.** Tick `heard_back` weekly. Without this the pipeline has no
feedback loop and will do the same thing forever, well or badly.

**Step 9 — Aggregate report.** The highest-value query in the project:

```sql
SELECT skill_key, COUNT(*) AS demand
FROM requirements r
JOIN jobs j ON j.id = r.job_id
WHERE r.kind = 'must'
  AND (r.matched_bullets IS NULL OR r.matched_bullets = '[]')
  AND j.role_family = 'platform'
GROUP BY skill_key
ORDER BY demand DESC;
```

A study plan derived from actual demand in the actual target market. Weekly
report, not a per-job feature.

### Backlog

- **Workday support** — biggest coverage gap (15 companies including all of FAANG).
- **Grow the company list** — the strongest lever on tier-1 volume.
- **Fix the `design` bare-keyword bug in `filters.py`** — same class as the
  `sales` fix (§6), still live. Catches "Frontend Engineer - Design Systems"
  as `not_engineering`.
- **Evidence-matching quality on the local model.** Observed the 3B Ollama
  model over-matching vague soft-skill requirements ("strong sense of
  ownership," "attention to detail") to 5-9 bullets at once, more generously
  than the evidence really supports. Didn't distort tier placement in the
  small sample checked, but worth a real look once the backlog is fully scored
  — the Claude escape hatch in §7a exists partly for this.
- **Soft dedupe** on `(company_id, title)` at review time.
- Sanity check that a company's job count hasn't collapsed between runs.
- Additional sources: USAJOBS, Adzuna, HN Algolia, RemoteOK.
- Parse LinkedIn job-alert emails from own inbox via IMAP (legitimate — they're
  sent to me).
- `contacts` prompt at review time to nudge toward referrals.
- Posting-count-over-time per company as a growth signal.
- Resolve the 15 fixable unresolved companies (HashiCorp, Sourcegraph, Retool,
  dbt Labs, Fly.io, BrowserStack, ...). Low priority.

---

## 11. Project discipline

A note to future me, and to any agent working on this repo.

This project is a comfortable place to hide from job hunting. Programming has
unambiguous feedback, total control, and no rejection. Job hunting has ambiguous
feedback, no control, and rejection as the median outcome. The trap is that this
project is *nominally about job hunting*, so working on it feels like progress on
the thing being avoided — it comes with a built-in alibi. And the scope is
unbounded: there is always a sixth ATS, a nicer review UI, another refactor.

**The rule: N applications per week regardless of the state of the code.** Build
only in the time left over. A week with shipped features and zero applications is
the signal; the fix is to stop building for a week, not to feel bad.

Warning signs: a working pipeline and a queue-abstraction refactor in progress;
three new data sources added and no applications since the project started.

Note that as of step 5 the pipeline **already produces a usable queue** — ~30
tier-1/2/3 roles, filtered and tagged. Reviewing and applying to those does not
require step 6. Scoring makes review faster; it isn't a precondition for it.

Worth saying the other side: this isn't wasted effort even in the hiding
scenario. It's a portfolio piece and a strong interview answer for exactly the
DevProd roles being targeted — building tooling that removes a bottleneck,
measured. Just cap it.

**Also outside the pipeline, and higher-yield than anything in it:** for every
job queued, spend two minutes checking whether anyone from a current or past
company works there. The `applications.referral` column exists for this. A
referral gets a human to read the resume instead of a keyword match deciding that
"in test" isn't "engineer." That two minutes is where conversion actually lives.

