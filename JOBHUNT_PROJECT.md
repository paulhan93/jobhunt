# Job Hunt Pipeline

A local-first pipeline that ingests job postings from public ATS APIs, filters them
against my criteria, scores fit against a structured resume bank, and surfaces a
ranked review queue. Submission stays manual by design.

This document is the source of truth for architecture and conventions. Read it
before changing the schema, adding a data source, or adding a pipeline stage.

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
- **No distributed architecture.** One person, ~6k postings, ~200 new/day.
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
2. `sdet` — Senior SDET, Software Engineer in Test, Test Automation
3. `sre` — SRE, Infrastructure Engineer (heavy overlap, good comp)
4. `swe` — general product engineering (wide net, lower conversion)

Review tier 1 exhaustively, tier 2 selectively, tiers 3–4 only when the queue is
thin. DevProd postings are roughly a fifth as common as product SWE postings —
narrow funnel, better conversion. That asymmetry is the main argument for the
pipeline: with a narrow niche you can't afford to miss listings.

### Notes for scoring

- "Aspiring senior" means **include both mid and senior postings.** Do not
  exclude `Software Engineer II/III`.
- Exclude `Staff` and above — those screen hard on prior staff-level scope.
- **Critical exclude:** `Manual QA`, `QA Analyst`, `QA Tester`, `Game Tester`,
  `Performance Tester`, `Localization QA`. These share keyword space with the
  target roles but are a step backward in comp and scope. Without these rules
  the queue fills with them.
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
 load_companies.py  ───>  companies table
        |
        v
   fetch.py  (cron, every 6h)  ───>  jobs table  (raw_json, first_seen_at, status)
        |
        v
  filter.py  (rules only, no LLM)  ──> ~85% rejected, role_family tagged
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
index is a perfectly good work queue at this volume.

**Polling, not streaming.** Job postings change slowly. Cron every 6h is
correct and cheaper than anything live.

**Checkpoint after the slow, unreliable stage.** `probe.py` writes to JSON
before anything touches the database. The expensive network step happens once;
the cheap parsing step can be rerun infinitely.

**Cheap filters run before expensive stages.** Deterministic rules kill ~85% of
rows for free. Only survivors reach a model. This ordering is most of the
compute budget.

**Normalize at the boundary.** Each ATS's response shape is absorbed by one
thin translation layer (`pipeline/ats.py`, then the per-ATS parsers). Downstream
code speaks one language. Never let `if ats == "lever"` leak into filter or
scoring code.

**Local model for volume, cloud model for quality.** Local (Ollama) handles
extraction and matching across every surviving posting — high volume, structured,
forgiving. A frontier API handles the few resumes actually sent — low volume,
high stakes. ~5–15 applications/week means cloud cost is cents.

**Model does extraction; arithmetic does judgment.** See §6.

---

## 4. Data model

Full schema lives in `schema.sql`. It is the source of truth; the `.db` file is
disposable output. `./reset.sh` drops and recreates.

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
    role_family   TEXT CHECK (role_family IN ('swe','sdet','platform','sre')),
    reject_reason TEXT,
    fit_score     REAL,
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

- **`UNIQUE (source, source_job_id)`** is the entire dedupe strategy. Combined
  with `ON CONFLICT ... DO UPDATE`, the fetcher becomes idempotent — running it
  twice does the same thing as running it once. Protect this property.
- **`raw_json NOT NULL`** so a fetcher can never forget it. When the parser
  turns out to have missed a field, the full history can be reprocessed instead
  of re-fetched. Postings close; the data would otherwise be gone.
- **`company_id` is nullable** on purpose, to leave room for sources with no
  curated company row (USAJOBS, HN). In SQLite, adding a nullable column later
  is one line; removing `NOT NULL` requires rebuilding the table.
- **Three timestamps.** `first_seen_at` is the only trustworthy freshness
  signal (see §5). `last_seen_at` powers closed detection. `closed_at` is a
  nullable timestamp rather than an `is_closed` boolean — answers "is it" and
  "when" for free.
- **`status` is a state machine** with a `CHECK`. A typo'd status is a job that
  silently vanishes from the pipeline with no error.
- **`attempts` / `last_error`** because extraction will crash on some JD. Worker
  takes `WHERE attempts < 3`, increments on entry, records the exception. Bounds
  failures and makes them visible.
- **`matched_bullets` as JSON** is a deliberate, known compromise. The pure form
  is a join table, but this array is only ever read whole for one requirement,
  never queried across. When you only read a blob whole, store it as a blob.
- **`ON DELETE CASCADE`** on requirements (can't exist without their job),
  deliberately **not** on applications (a bug deleting a job should fail loudly
  rather than erase application history).

### What is not in the database

The resume bullet bank lives in `resume.yaml`, not a table. Heuristic: **if a
human edits it and the program only reads it, it's a file.** Config in a file
gets version control, normal editing, and no migration to add a field.

---

## 5. ATS reference

All endpoints are public, unauthenticated GETs. These are the endpoints
companies' own careers pages call — no scraping, no proxies, no ToS issue.

| ATS | Endpoint |
|---|---|
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` |
| Lever | `https://api.lever.co/v0/postings/{slug}?mode=json` |
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true` |
| SmartRecruiters | `https://api.smartrecruiters.com/v1/companies/{slug}/postings` |
| Workable | `https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true` |

### Quirks — each of these has already caused a bug

**Response shape differs.** Lever returns a bare array. SmartRecruiters names
its list `content`. Greenhouse, Ashby, and Workable use `jobs`. Handled by
`count_jobs()` in `pipeline/ats.py`.

**HTTP 200 does not mean the request was valid.** Greenhouse, Lever, and Ashby
404 on an unknown slug. **Workable and SmartRecruiters return 200 with an empty
result set**, which made the probe confidently assign `workable/microsoft` and
`smartrecruiters/google`. Hence `STRICT_404 = {"greenhouse", "lever", "ashby"}`
— only trust a zero-job response from those three. When probing for existence,
require *positive* evidence, never absence-of-error.

**Pagination is five different ideas.** Greenhouse and Ashby return everything
in one response. SmartRecruiters uses `limit`/`offset` and caps at 100 — Canva
and Wise both returned exactly 100, which is a page cap, not a coincidence.
Workable's newer endpoint uses an opaque continuation token.

**Dates lie in different ways.** Lever gives `createdAt` in epoch milliseconds.
Greenhouse gives `updated_at`, which changes on any edit, not on publish.
Workday gives `postedOn` as English text ("Posted 3 Days Ago"). **Never trust
source timestamps** — set `first_seen_at` on insert and diff on a content
fingerprint instead.

**Exclude the description from any change fingerprint.** ATS platforms rewrite
whitespace and tracking params in descriptions constantly; including them flags
every job as updated every day.

**Slug discovery is the real problem, not fetching.** There is no master list,
no search endpoint. Slugs are usually the lowercased name with spaces stripped,
but: `Harness → harnessinc`, `Sonar → sonarsource`, `Notion → notionhq`,
`Nx → nrwl`. Generic generation gets ~65%; the rest are cheaper to resolve by
hand (open a careers page, click into a posting, read the ATS and slug out of
the URL) than to automate.

**Politeness.** ~0.4s between requests, real email in the User-Agent, backoff on
429/5xx, `If-Modified-Since`/`ETag` where supported. These endpoints are free and
unauthenticated, which is exactly why not to hammer them.

### Workday (not yet implemented)

No documented public API, but every Workday careers site is an SPA calling a
JSON endpoint directly:

```
POST https://{tenant}.wd5.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
Content-Type: application/json
{"limit": 20, "offset": 0, "searchText": ""}
```

Per-tenant and fiddly, but it unlocks a large block of high-value targets at
once. Well-scoped addition once the core pipeline runs.

---

## 6. Fit scoring

The model does **extraction only**. Judgment is arithmetic. This is the most
important design decision in the project.

Asking an LLM to rate anything 0–100 returns 72–85 for nearly everything — a
number that feels precise and means almost nothing. Instead:

**6a. Extract requirements** (local model, schema-constrained via Ollama's
`format` param). One row per requirement into `requirements`:
`{text, kind: must|nice, skill_key, years_required}`. Pure reading
comprehension — no judgment about the candidate.

`skill_key` must be normalized against a controlled vocabulary
(`python`, `ci-cd`, `build-systems`, `kubernetes`, `observability`,
`test-infrastructure`, `developer-productivity`, `internal-tools`, ...).
Free-text skill names will wreck the aggregate report in §8.

**6b. Match evidence** (local model, per requirement, constrained to valid
bullet IDs from `resume.yaml`). Returns supporting bullet IDs or empty. Because
output is constrained to an existing ID set, the model *structurally cannot*
invent evidence.

**6c. Score** — no model involved:

```
must_hit = matched must-haves / total must-haves
nice_hit = matched nice-to-haves / total nice-to-haves
fit      = 100 * (0.75 * must_hit + 0.25 * nice_hit)
```

Years is fully deterministic: sum date ranges of roles tagged with that
`skill_key` in the bank, compare to `years_required`. If any must-have fails on
years by more than ~2x, cap the recommendation at `stretch` regardless of score.

Recommendation tiers: `apply` / `stretch` / `skip`.

**Why this matters more than the number:** every point traces to a
requirement → bullet edge. When a score looks wrong you can see exactly which
requirement went unmatched and whether the model missed evidence that exists.
That debuggability is the payoff. The gap lists are more useful than the score.

---

## 7. Resume bank

`resume.yaml` holds a bullet bank deliberately 3–4× larger than any resume
actually sent — target ~8 bullets per role, each tagged.

```yaml
experience:
  - company: Acme Corp
    title: Software Engineer in Test
    start: 2022-03
    end: 2025-11
    bullets:
      - id: acme-1
        text: "Cut CI feedback time from 40 to 9 minutes across 60 engineers by
                introducing test sharding and a remote build cache."
        tags: [ci-cd, build-systems, developer-productivity, python]
      - id: acme-2
        text: "Built a Playwright framework covering 40 checkout paths; reduced
                release regression from 2 days to 3 hours."
        tags: [test-infrastructure, playwright, ci-cd, developer-productivity]
```

### Why a bank instead of prose

The LLM's job is **selection, ordering, and light rewording within a bullet** —
never invention. It returns bullet IDs, validated against the known set:

```python
class TailoredResume(BaseModel):
    summary: str                    # only free-form field
    selected_bullets: list[str]     # validated against BANK_IDS
    skills_order: list[str]
    reword: dict[str, str] = {}     # id -> tightened phrasing, same claim
```

Any reworded bullet is diffed against the original and shown before it ships. If
a number changed, it gets caught. Never let a model regenerate employment
history from prose — it will quietly invent a job.

### SDET → DevProd translation (important)

Write every bullet in **engineering** terms, not QA terms. This determines
whether the matcher finds evidence for platform requirements.

- Weak: "Wrote automated tests for the checkout flow using Playwright."
- Strong: "Built a Playwright framework covering 40 checkout paths; cut release
  regression from 2 days to 3 hours."

Same work; the second matches `python`, `ci-cd`, `framework-design`, and
`developer-productivity`. Quantify in **time saved for other engineers**, not
coverage percentages. "Increased test coverage to 85%" is a QA metric; "cut CI
feedback time from 40 to 9 minutes across 60 engineers" describes the same
competence in platform terms.

Surface general engineering work that isn't on the current resume: internal
tools, CI pipeline changes, mock services, flakiness dashboards, test harnesses
that are real distributed systems. SDETs almost always have more of this than
their resume shows.

### Rendering

Typst, not LaTeX. Single column, standard headings, real selectable text, no
tables or graphics. The "ATS auto-rejects on formatting" idea is largely myth —
but recruiters *do* run keyword searches inside the ATS, so relevant keywords
matter. Optimize for the human reading it.

---

## 8. Build plan

### Done

**Step 1 — Schema.** `schema.sql` + `reset.sh` + `pipeline/db.py`. Rebuildable
from scratch in one command.

**Step 2 — Company list and ATS probe.** `data/companies.txt` (101 names),
`pipeline/ats.py`, `scripts/probe.py`, `scripts/load_companies.py`.

Current state: **71 companies resolved** — greenhouse 40, ashby 24, lever 4,
smartrecruiters 3. Roughly 6,000 live postings available.

15 unresolved and settled (Workday / homegrown): Google, Microsoft, Amazon,
Apple, Meta, Netflix, Nvidia, GitHub, Shopify, Atlassian, Slack, DoorDash,
Intel, Nike, AWS Elemental — marked `no_public_ats` so the probe skips them.

15 unresolved but potentially fixable: Applitools, Automattic, BrowserStack,
Chronosphere, Fly.io, HashiCorp, LambdaTest, Nx, Qase, Remitly, Retool,
Sourcegraph, Tricentis, Zillow, dbt Labs. Low priority.

Core DevProd targets all resolved: Grafana Labs, Harness, Sonar, Sauce Labs,
Datadog, Sentry, CircleCI, Buildkite, LaunchDarkly, Gradle, JetBrains, Docker,
Temporal, Honeycomb, Postman.

### Next

**Step 3 — Fetchers.** One parser per ATS returning a shared `NormalizedJob`.
Upsert via `ON CONFLICT (source, source_job_id)`. Greenhouse needs
`?content=true` for descriptions; SmartRecruiters needs `limit`/`offset` paging.
*Done when:* one command populates `jobs` from all 71 companies and a second run
inserts nothing new.

**Step 4 — Closed detection + cron.** After fetching a board, stamp `closed_at`
on any of that company's open jobs absent from the response. Then
`0 */6 * * *` with output to a logfile.
*Done when:* running unattended for 24h with a clean log.

**Step 5 — Cheap filter.** Pure Python on `status = 'new'`. Sets `filtered` or
`rejected` with a reason, and tags `role_family`. Order rules cheapest-first:
title regex → location/remote → hard excludes → salary floor. **Log every
rejection reason** — the failure mode is a filter silently eating everything
good.
*Done when:* reviewing 200 rejections and agreeing with ~95%.

**Step 6 — Extraction + scoring.** See §6. Requires `resume.yaml` to exist
first — write the bank before the code.
*Done when:* `datasette serve jobs.db`, sort by `fit_score` desc, and the top 10
are jobs worth considering.

**Steps 1–6 are the MVP. Stop and use it for at least a week before building
more.**

### Later, in order

**Step 7 — Tailoring.** Cloud model selects bullet IDs → Typst → PDF. Diff any
reworded bullet before it ships.

**Step 8 — Outcomes.** Tick `heard_back` weekly. Without this the pipeline has
no feedback loop and will do the same thing forever, well or badly.

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

A study plan derived from actual demand in the actual target market. This is the
thing the system can do that nothing else can. Weekly report, not a per-job
feature.

**Backlog:** Workday support · USAJOBS / Adzuna / HN Algolia / RemoteOK sources ·
parse LinkedIn job-alert emails from own inbox via IMAP (legitimate: they're
sent to me) · `contacts` prompt at review time to nudge toward referrals ·
posting-count-over-time per company as a growth signal.

---

## 9. Layout and conventions

```
jobhunt/
├── PROJECT.md              this file
├── .gitignore              .venv/ *.db *.db-wal *.db-shm
├── schema.sql              source of truth for the DB
├── reset.sh                drop + recreate from schema.sql
├── requirements.txt
├── resume.yaml             bullet bank (step 6+)
├── data/
│   ├── companies.txt        hand-curated, # comments supported
│   └── probe_results.json   checkpoint from probe.py
├── pipeline/               importable library
│   ├── __init__.py
│   ├── db.py               get_conn()
│   ├── ats.py              endpoints, PROBE_ORDER, STRICT_404, count_jobs, slug_candidates
│   ├── fetch.py            per-ATS parsers → NormalizedJob (step 3)
│   ├── filters.py          rules + role_family tagging (step 5)
│   ├── extract.py          Ollama calls (step 6)
│   └── score.py            arithmetic (step 6)
└── scripts/                entry points
    ├── probe.py
    ├── fix_slugs.py
    ├── load_companies.py
    ├── fetch_all.py
    └── report.py
```

**`pipeline/` is imported, `scripts/` is executed.** Library vs entry point.
Scripts stay thin — arg parsing and a call into the library.

### Conventions

- **Run scripts as modules from the project root:** `python -m scripts.probe`.
  Not `python scripts/probe.py` — that puts `scripts/` on the import path and
  `from pipeline.db import ...` fails.
- **Always use `pipeline.db.get_conn()`**, never `sqlite3.connect()` directly.
  `PRAGMA foreign_keys` is **per-connection and defaults OFF** in SQLite — miss
  it and every `REFERENCES` / `ON DELETE CASCADE` is decoration that silently
  permits orphaned rows. `get_conn()` also sets `row_factory = sqlite3.Row` so
  rows behave like dicts.
- **`with get_conn() as conn:`** commits on success, rolls back on exception.
  (It does not close the connection — fine for short scripts.)
- **Every stage must be idempotent and resumable.** Write results incrementally,
  skip already-done work on entry. Rerunning must be free.
- **Network functions return verdicts, never raise.** When loop iterations are
  independent, failure is a value the loop handles, not an exception that ends a
  71-company run.
- **Ambiguous outcomes get their own category.** `hit` / `empty` / `miss` /
  `no_public_ats` — not a forced binary. Same will apply to the filter's
  "reject" vs "not sure".
- **Never assume the shape of a response you didn't create.** `isinstance`
  checks on external JSON are not paranoia; it can be `null`, an error object,
  or HTML with a JSON content-type.
- **Schema changes:** while the DB holds nothing precious, edit `schema.sql` and
  run `./reset.sh`. Once there's real history, switch to versioned migrations.

---

## 10. Project discipline

A note to future me, and to any agent working on this repo.

This project is a comfortable place to hide from job hunting. Programming has
unambiguous feedback, total control, and no rejection. Job hunting has ambiguous
feedback, no control, and rejection as the median outcome. The trap is that this
project is *nominally about job hunting*, so working on it feels like progress on
the thing being avoided — it comes with a built-in alibi. And the scope is
unbounded: there is always a sixth ATS, a nicer review UI, another refactor.

**The rule: N applications per week regardless of the state of the code.** Build
only in the time left over. A week with shipped features and zero applications
is the signal; the fix is to stop building for a week, not to feel bad.

Warning signs: week five with a working pipeline and a queue-abstraction
refactor in progress; three new data sources added and no applications since the
project started.

Worth saying the other side: this isn't wasted effort even in the hiding
scenario. It's a portfolio piece and a strong interview answer for exactly the
DevProd roles being targeted — building tooling that removes a bottleneck,
measured. Just cap it.

**Also outside the pipeline, and higher-yield than anything in it:** for every
job queued, spend two minutes checking whether anyone from a current or past
company works there. The `applications.referral` column exists for this. A
referral gets a human to read the resume instead of a keyword match deciding
that "in test" isn't "engineer." That two minutes is where conversion actually
lives.
