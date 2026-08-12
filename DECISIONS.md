# Decisions

Architecture decision records for this project. One entry per real decision:
what was chosen, what was rejected, why, and what it costs.

---

## 1. Automate triage and drafting; keep submission manual

**Date:** 2026-08-11

**Decision:** The pipeline finds, filters, scores, and drafts. A human reviews
every job and submits every application by hand.

**Alternatives considered:** Full auto-apply with headless browser form filling.

**Why:** Application forms are per-company, have knockout questions and custom
fields, and break constantly — high maintenance for low value. More importantly,
mass-submitting generated applications is a pattern recruiting teams detect, and
being flagged as a bulk applicant at a company I actually wanted is a permanent
cost. The 90% time win is in triage and drafting; the last mile is cheap by hand.

**Cost:** Still ~5 minutes of manual work per application. Accepted — that's also
the step where I'd notice a role isn't what the JD implied.

---

## 2. Public ATS APIs instead of scraping LinkedIn or Indeed

**Date:** 2026-08-11

**Decision:** Source all postings from documented public JSON endpoints
(Greenhouse, Lever, Ashby, SmartRecruiters, Workable).

**Alternatives considered:** Scraping LinkedIn/Indeed job search; a third-party
aggregator API.

**Why:** Most companies rent their careers page from one of ~8 ATS vendors, and
each serves job data as public unauthenticated JSON — the same endpoint the
company's own careers page calls. No HTML parsing, no proxies, no ToS violation,
no breakage when a page is redesigned. LinkedIn has no public endpoint, an
explicit ToS prohibition, and well-funded anti-bot defenses; scraping it risks
the account I need for the job search itself.

**Cost:** Coverage gap. Companies on Workday or homegrown systems (Google,
Microsoft, Amazon, Apple, Meta, Netflix, Nvidia, GitHub, Shopify, Atlassian) are
invisible to the pipeline and need a manual weekly check. This is the largest
known limitation.

---

## 3. SQLite as both store and queue

**Date:** 2026-08-11

**Decision:** One SQLite file. A `status` column plus a partial index is the work
queue. Cron is the scheduler.

**Alternatives considered:** Postgres + Redis + Celery; a hosted queue.

**Why:** One user, ~7,000 postings, ~200 new per day, batch processing every 6
hours. Nothing about this needs concurrency, network-attached storage, or a
message broker. SQLite means zero setup, zero running services, a single file to
back up, and Datasette works on it for free. If this ever outgrows SQLite I'll
have had a job for a long time.

**Cost:** No concurrent writers, so pipeline stages run sequentially. Irrelevant
at this scale.

---

## 4. Schema is code; the database is disposable output

**Date:** 2026-08-11

**Decision:** `schema.sql` is the source of truth, committed to git. `jobs.db` is
gitignored. `./reset.sh` drops and recreates from scratch.

**Alternatives considered:** Creating tables interactively in the sqlite3 shell
and treating the `.db` file as the artifact.

**Why:** Code is cheap to change; data is expensive. A database built by hand has
no recipe — can't be reproduced, diffed, reviewed, or moved to another machine,
and every `ALTER TABLE` is invisible history. With the schema in a file, changing
my mind costs ten seconds: edit, `./reset.sh`, done. I did this repeatedly while
building steps 2–4.

**Cost:** Only free while the database holds nothing precious. Once there's real
job history, this converts to versioned migrations.

---

## 5. Store the full raw API response on every job

**Date:** 2026-08-11

**Decision:** `jobs.raw_json TEXT NOT NULL` holds the complete untouched response
object for each posting.

**Alternatives considered:** Store only the parsed fields.

**Why:** The parser will turn out to be wrong. When I discover a field I should
have captured (Ashby exposes team; SmartRecruiters exposes `experienceLevel` and
a `Role level` custom field), I can reprocess the entire history instead of
re-fetching — and re-fetching is impossible, because closed postings are gone
from the board forever. `NOT NULL` means a fetcher can never silently forget it.

**Cost:** Database size. At ~7k rows this is single-digit megabytes. Irrelevant.

---

## 6. Never trust source timestamps; stamp `first_seen_at` on insert

**Date:** 2026-08-11

**Decision:** Freshness comes from our own `first_seen_at`, set when a row is
first written. Source date fields are stored in `raw_json` but not used for
logic.

**Alternatives considered:** Use each ATS's posted/created/updated date.

**Why:** They disagree fundamentally. Lever gives `createdAt` in epoch
milliseconds. Greenhouse gives `updated_at`, which changes on any edit rather
than on publish. Workday gives `postedOn` as English text ("Posted 3 Days Ago").
Building freshness logic on top of that means parsing English and three date
formats to get an unreliable answer.

**Cost:** Jobs posted before I added their company all look "new" on first
fetch — a one-time distortion per company, which is why the first run showed
7,181 new.

---

## 7. Dedupe on `UNIQUE (source, source_job_id)` with an upsert

**Date:** 2026-08-11

**Decision:** `INSERT ... ON CONFLICT (source, source_job_id) DO UPDATE`. The
update refreshes `last_seen_at` and the mutable fields but deliberately does not
touch `status`, `first_seen_at`, or `fit_score`.

**Alternatives considered:** Fingerprinting on `(company, title, location)`;
hashing the description; delete-and-reinsert each cycle.

**Why:** Every ATS assigns a stable ID per posting, unique within that ATS — pair
it with `source` and it's globally unique. This makes the fetcher **idempotent**:
running it twice does the same thing as running it once, which is what makes a
6-hour cron safe. Verified — second consecutive run reported 1 new out of 7,182.

Description hashing was explicitly rejected: ATS platforms rewrite whitespace and
tracking parameters in descriptions constantly, so every job would look updated
every day.

Not resetting `status` matters — otherwise a job I'd already filtered or reviewed
would return to the queue on the next fetch.

**Cost:** The same role posted separately on two ATSes would appear twice. Hasn't
happened; would need cross-source fingerprinting to fix.

---

## 8. Detect closed jobs by absence, in the fetch loop

**Date:** 2026-08-11

**Decision:** After fetching a company's board, stamp `closed_at` on any of that
company's still-open jobs whose ID was not in the response. The upsert clears
`closed_at` if a job reappears.

**Alternatives considered:** A separate step run on its own schedule; trusting a
status field from the ATS.

**Why:** No ATS reports closed jobs — the board only ever contains open ones, and
filled roles simply stop appearing. Absence is the only available signal. It has
to live in the fetch loop because that's the only moment we know what a board
currently contains. Without it, filled roles sit in the queue looking open
forever and I'd waste review time and a tailored resume on a role that closed
weeks ago.

**Cost:** A partial API response (server error mid-page, truncated payload) would
mass-close jobs that are actually open. Mitigated by the reopen path, but a
sanity check — refuse to close more than ~30% of a company's jobs in one pass —
would be worth adding.

---

## 9. Checkpoint the probe to JSON before touching the database

**Date:** 2026-08-11

**Decision:** `probe.py` writes findings to `data/probe_results.json` after every
company. A separate `load_companies.py` reads that file into SQLite.

**Alternatives considered:** One loop that probes and inserts in the same pass.

**Why:** Network calls are slow and flaky; database writes are fast and
deterministic. Coupling them means a bug in the insert forces re-running ~1,700
HTTP requests. Writing after each company also makes the probe resumable — kill
it and rerun, and it skips what already resolved. General rule: **when a pipeline
has one slow, unreliable stage, checkpoint immediately after it.**

**Cost:** Two commands instead of one, and a JSON file that can drift from the
database. Acceptable — the loader's upsert makes reloading free.

---

## 10. Only trust "zero jobs" from ATSes that 404 properly

**Date:** 2026-08-11

**Decision:** `STRICT_404 = {greenhouse, lever, ashby}`. A 200 with zero jobs
counts as a real-but-empty board only for those three. From Workable and
SmartRecruiters, zero jobs means the slug is probably invalid.

**Alternatives considered:** Treat any 200 as proof the board exists (the
original implementation).

**Why:** This was a real bug. Workable and SmartRecruiters return HTTP 200 with an
empty result set for a nonexistent slug, so the probe confidently assigned
`workable/microsoft` and `smartrecruiters/google` — 38 false positives that would
have been written to the database as fact and polled forever. Greenhouse, Lever,
and Ashby return a proper 404.

**Lesson:** HTTP 200 does not mean the request was valid; it means the server
chose to respond successfully. When probing for existence, require **positive
evidence**, never absence-of-error.

**Cost:** None. Strictly more correct.

---

## 11. Resolve unmatched slugs by hand rather than automating

**Date:** 2026-08-11

**Decision:** Generic slug generation (lowercase, strip spaces/suffixes, try
`-hq`) resolves ~65%. The rest get looked up manually by opening the careers page
and reading the ATS and slug out of a posting URL.

**Alternatives considered:** More heuristics; a search-engine lookup step.

**Why:** The remaining misses are genuinely arbitrary — `Harness → harnessinc`,
`Sonar → sonarsource`, `Nx → nrwl`, `Notion → notionhq`. No heuristic finds
these; only the actual careers page knows. 15 minutes of clicking resolved 5 more
companies. Recognizing which parts of a problem are cheaper by hand is an
engineering decision, not a cop-out.

**Cost:** Manual step whenever companies are added. Bounded and rare.

---

## 12. Mark permanently-unresolvable companies instead of retrying

**Date:** 2026-08-11

**Decision:** Confirmed Workday/homegrown companies get `status:
"no_public_ats"`, and the probe's skip check includes it.

**Why:** 15 companies × 5 slug candidates × 5 ATSes ≈ 375 wasted requests per
run, against endpoints I'm asking to be polite to. Recording the negative result
is as valuable as recording the positive one.

**Cost:** If one of them moves to a public ATS, I won't notice. Acceptable — they
get a manual weekly check anyway.

---

## 13. Normalize every ATS to one internal shape at the boundary

**Date:** 2026-08-11

**Decision:** Each ATS has exactly one parser returning `NormalizedJob`. All
vendor-specific knowledge — response shape, field names, HTML stripping,
pagination — lives in `pipeline/fetch.py` and nowhere else.

**Alternatives considered:** Store each ATS's native shape and branch on `source`
downstream.

**Why:** Five vendors disagree about everything: Lever returns a bare array while
others wrap in `jobs` or `content`; Greenhouse needs `?content=true` or
descriptions come back empty; Lever splits descriptions across `descriptionPlain`
plus a `lists` array; Ashby is the only one with structured salary; only
SmartRecruiters paginates. If `if source == "lever"` leaks into the filter and
scoring code, that's five vendors' worth of special cases smeared across the
codebase. One translation layer at the edge means everything downstream speaks
one language.

**Cost:** Adding an ATS means writing a parser. That's the right amount of work.

---

## 14. Defensive `isinstance` checks on all external JSON

**Date:** 2026-08-11

**Decision:** Never assume the type of a field from an external API. Check before
calling `.get()`.

**Why:** Also a real bug. `parse_smartrecruiters` crashed on Canva and Wise with
`'str' object has no attribute 'get'` — I wrote `(j.get("ref") or {}).get("jobAd")`
assuming `ref` was a nested object, but SmartRecruiters returns it as a plain URL
string. Notably, this was the one parser written without ever seeing a live
response, because the probe only counted rows rather than parsing them.

**Lesson:** Inspect a real response before writing a parser for it. External JSON
can be `null`, a string where you expected an object, an error object, or HTML
with a JSON content-type.

**Cost:** More verbose parsers. Worth it.

---

## 15. Deactivate SmartRecruiters companies rather than adding detail calls

**Date:** 2026-08-11

**Decision:** Set `active = 0` for the SmartRecruiters companies (Canva, Wise,
Uber). Fix the parser crash first so the run is clean.

**Alternatives considered:** A per-job detail call to fetch descriptions.

**Why:** The SmartRecruiters list endpoint has no description field, and
descriptions are what step 6 extracts requirements from — so those ~500 jobs
would be invisible to scoring anyway. Getting them means ~500 extra HTTP requests
per fetch cycle for 2 of 71 companies, neither of which is a DevProd target. Bad
trade. Also deleted the Uber row: 1 posting on SmartRecruiters is a stale or
squatted account, not Uber's real board.

**Cost:** Two companies uncovered. Reversible in one UPDATE if their DevProd
roles ever matter.

---

## 16. Fail per-company, never per-run

**Date:** 2026-08-11

**Decision:** Network and parse errors are caught per company, logged, counted,
and the loop continues. `probe_one` returns a verdict dict for every outcome
rather than raising.

**Why:** One company's board being down must not kill a 71-company run.
Demonstrated: the Canva and Wise crashes cost 2 companies, not 7,181 jobs. When
loop iterations are independent, failure should be a **value the loop handles**,
not an exception that ends it.

**Cost:** Errors are easy to ignore if nobody reads the log. Mitigated by the
error count in the summary line.

---

## 17. Ambiguous outcomes get their own status, not a forced binary

**Date:** 2026-08-11

**Decision:** The probe returns `hit` / `empty` / `miss` / `no_public_ats` rather
than success/failure.

**Why:** "Board exists but has zero open roles" is genuinely different from
"slug doesn't exist." Collapsing them into `miss` would discard a correct slug
and re-probe it forever. Collapsing them into `hit` was the bug in decision 10.
Distinct states also let the resumability check treat each appropriately.

**Cost:** More states to handle. Same principle will apply to the filter's
"reject" vs "not sure."

---

## 18. Curate the company list by hand; keep it broad

**Date:** 2026-08-11

**Decision:** ~100 hand-picked companies in `data/companies.txt`. Broad on
companies, narrow in the filter.

**Alternatives considered:** Ingest every company on every ATS; keep a tight list
of ~25 dream companies.

**Why:** Filter test for inclusion: *would I take a call from them tomorrow?*
Every marginal company is a permanent tax on every review session.

But not too narrow, for two reasons. DevProd postings are roughly a fifth as
common as product SWE postings — narrowing both company list and role filter
leaves an empty queue. And DevProd teams only exist where there's enough internal
pain to staff one, which means larger engineering orgs. Counterintuitively the
list should skew *bigger* than a product SWE's, not smaller.

**Cost:** An hour of manual curation, repeated whenever the list grows. Highest
leverage hour in the project.

---

## 19. `resume.yaml` is config, not a database table

**Date:** 2026-08-11

**Decision:** The bullet bank lives in a YAML file in git, not in SQLite.

**Why:** A database is for data the program writes. The bullet bank is data *I*
write, by hand, a few times a month, and the program only reads. Keeping it as a
file gets version control (git log shows how the resume evolved), editing in a
normal editor, and no migration to add a field. Heuristic: **if a human edits it
and the program only reads it, it's a file.**

**Cost:** No relational queries over bullets. Not needed.

---

## 20. Bullet bank with ID selection, not prose regeneration

**Date:** 2026-08-11 *(design decided; implemented at step 7)*

**Decision:** `resume.yaml` holds ~4× more tagged bullets than any single resume
uses. Tailoring returns **bullet IDs** validated against the known set, plus
optional rewording that gets diffed against the original before shipping.

**Alternatives considered:** Give the model the master resume and the JD and ask
for a tailored version.

**Why:** A model regenerating employment history from prose will eventually
invent a job, a date, or a metric — and I would send that to a recruiter.
Constraining output to an existing ID set means fabrication is **structurally
impossible** rather than something I have to catch by proofreading. The model's
job becomes selection and ordering, which is what it's actually good at.

**Cost:** Bullets must be written in advance, and quality is capped by the bank.
That's fine — writing the bank once is reusable work.

---

## 21. Model extracts; arithmetic judges

**Date:** 2026-08-11 *(design decided; implemented at step 6)*

**Decision:** The local model extracts requirements from the JD and matches them
to bullet IDs. The fit score is then computed:

```
fit = 100 * (0.75 * must_hit + 0.25 * nice_hit)
```

Years is fully deterministic — sum date ranges of roles tagged with that skill
and compare to `years_required`. No model involved in scoring.

**Alternatives considered:** Ask the model directly for a 0–100 fit score.

**Why:** LLM numeric self-ratings cluster in the 72–85 band for almost
everything — a number that looks precise and carries little signal. Worse, it's
opaque: a wrong score gives you nothing to inspect. With extraction plus
arithmetic, every point traces to a specific requirement → bullet edge, so I can
see exactly which requirement went unmatched and whether the model missed
evidence I actually have. **That debuggability matters more than the number**, and
the gap lists are the genuinely useful output regardless of the score.

**Cost:** Two model calls per job instead of one, and the weights are arbitrary.
Fine — the ranking is what matters, not the absolute value.

---

## 22. Cheap deterministic filters before any model call

**Date:** 2026-08-11 *(design decided; implemented at step 5)*

**Decision:** Rules-only filter on `status = 'new'` rejects ~85% of postings
before extraction. Ordered cheapest-first: title excludes → role family match →
location → comp floor.

**Alternatives considered:** Score everything with the local model and sort.

**Why:** ~200 new postings a day, of which maybe 25 are plausible. The rejects
are unambiguous — Manual QA, Staff+, wrong continent — so no judgment is needed
and no quality is lost. Filtering first means 25 model calls instead of 200. This
one ordering decision is most of the compute budget.

**Cost:** A too-aggressive rule silently eats good jobs. Mitigated by storing
`reject_reason` on every rejection and reviewing them for the first week, and by
making the stage rerunnable so rules can be retuned against real data.

---

## 23. Tag `role_family` and review each separately

**Date:** 2026-08-11 *(design decided; implemented at step 5)*

**Decision:** Every surviving job is tagged `platform` / `sdet` / `sre` / `swe`.

**Why:** I'm pursuing multiple doors from SDET toward senior, and they aren't
comparable — 60% fit for a DevProd role means something different from 60% for a
product SWE role. Separate families let me review tier 1 exhaustively and tier 4
only when the queue is thin, and let me eventually answer *which door is actually
responding to me*.

**Cost:** One more column and a classification step that will sometimes be wrong.

---

## 24. Local model for volume, cloud model for what a human reads

**Date:** 2026-08-11 *(design decided; implemented at steps 6–7)*

**Decision:** Ollama handles extraction and matching for every job surviving the
filter. A frontier API handles the summary paragraph, rewording, and cover
letters for jobs I actually apply to.

**Alternatives considered:** All cloud, or all local.

**Why:** Different tasks. Extraction is high-volume, structured, and forgiving —
schema-constrained decoding matters more than model size. Writing a summary a
recruiter reads is low-volume and high-stakes. At ~5–15 applications a week,
cloud cost is cents; paying for quality on the artifact a human actually reads is
the right trade, and optimizing it further is a waste of attention.

**Cost:** Two model integrations to maintain.

---

## 25. Datasette instead of building a UI

**Date:** 2026-08-11

**Decision:** `datasette serve jobs.db` is the review interface until it
demonstrably isn't enough.

**Alternatives considered:** FastAPI + htmx review app.

**Why:** Zero code for a browsable, sortable, filterable UI over the exact data
model I already have — and it shows the SQL behind every click, which is how I'll
learn SQL against data I care about. A custom UI is the most tempting and least
necessary thing to build here; it's pure scope creep dressed up as progress.

**Cost:** Not optimized for fast review of many jobs. Revisit if review becomes
the bottleneck — and only then.

---

---

## 26. Verify a live API response before writing its parser

**Date:** 2026-08-11

**Decision:** Before writing or trusting a parser, curl the real endpoint and
inspect the actual field structure.

**Why:** Two bugs from the same root cause. `parse_smartrecruiters` assumed `ref`
was a nested object; it's a plain URL string, which crashed Canva and Wise.
`parse_ashby` was written against an assumed compensation shape — the parser
happened to be right, but the URL was missing `?includeCompensation=true`, so all
1,310 Ashby jobs stored NULL comp. Both parsers were written without ever seeing a
response, because the probe only *counted* jobs rather than parsing them.

Inspecting the real Ashby payload also surfaced two things I wouldn't have
guessed: components can be typed `Salary` with `minValue: null`, and `interval`
distinguishes `1 YEAR` from hourly — so an unguarded parser could store `85` in a
field the comp filter compares against an annual floor.

**Cost:** One curl per endpoint. Trivial next to the debugging it prevents.

---

## 27. Raw storage protects against parser bugs, not against bad requests

**Date:** 2026-08-11

**Decision:** Re-fetch rather than backfill when a field was never requested.

**Why:** Decision 5 stores `raw_json` so parser mistakes can be corrected by
reprocessing history. But Ashby comp wasn't a parsing failure — the URL never
asked for it, so the data wasn't in `raw_json` either. A backfill script would
have found nothing. This is the boundary of what raw storage buys: it protects
against misreading a response, not against requesting the wrong thing.

**Cost:** One extra fetch cycle. Cheap here; would not be if the data had expired.

---

## 28. Retry transient failures inside the fetch, not by re-running

**Date:** 2026-08-11

**Decision:** `_get` retries up to 3 times with exponential backoff on timeouts
and 429/5xx. 4xx raises immediately. `TIMEOUT` raised from 20s to 30s.

**Alternatives considered:** Leave it to the next cron cycle.

**Why:** Grafana Labs — a 145-job board and a genuine DevProd target — timed out
and was skipped entirely. Waiting 6 hours for the next cycle is a long gap for a
failure that a 2-second retry fixes. Retrying only on transient classes matters:
re-requesting a 404 is pointless, so those fail fast.

**Cost:** A truly dead endpoint takes ~6s longer to give up. Negligible.

---

## 29. Refuse to close jobs when a response looks truncated

**Date:** 2026-08-11

**Decision:** Skip closed detection for a company if the fetched job count is
under 70% of its currently-open count. Log a WARN instead.

**Alternatives considered:** Trust every 200 response (the original behavior).

**Why:** This closes an open question from decision 8. Closed detection infers
"filled" from absence, so a successful-but-partial response — server error
mid-page, truncated payload, a board temporarily emptied during migration —
would stamp `closed_at` on hundreds of live jobs. A board legitimately shrinking
30% in six hours is nearly impossible; a truncated response is entirely possible.

Confirmed with real data: closed detection fired correctly for the first time
this cycle (10 closed across Block, Coinbase, Lyft, Reddit, Stripe, Vercel), so
the mechanism works — which is exactly when it's worth bounding its blast radius.
The guard also covers Deel, Plaid, Snyk, and Expensify, whose boards return 0
jobs.

**Cost:** A company that genuinely does a mass layoff or board migration keeps
stale open rows until manually cleared. Preferable to the alternative — false
"closed" silently removes jobs from review, while false "open" is visible.

---

## 30. Deactivate SmartRecruiters rather than fix its description gap

**Date:** 2026-08-11 *(supersedes the deferred half of decision 15)*

**Decision:** `active = 0` for Canva and Wise; Uber row deleted. Parser crash
fixed anyway so the code isn't left with a known bug.

**Why:** Confirmed in practice — the run is now clean and the error count dropped
from 2 to 1 (the remaining one was an unrelated timeout). Fixing the parser
without keeping the companies active is deliberate: a known crash left in the
codebase is a trap for future me, even in dead code.

**Cost:** Two companies uncovered. One UPDATE to reverse.

---

## 32. Rules as data, not control flow

**Date:** 2026-08-11

**Decision:** `_REJECTS` and `_FAMILIES` are ordered lists of
`(name, compiled_pattern)` tuples. `classify()` walks them and returns on first
match. Adding a rule is a list entry; removing one is deleting a line.

**Alternatives considered:** A `Filter` class with a method per rule; a chain of
`if/elif` in `classify()`.

**Why:** The rules will change constantly as real rejections get reviewed — they
were retuned four times in one sitting. A class would be ceremony around a single
method. An if/elif chain buries priority in control flow, where changing the
order means editing logic rather than reordering data. As lists, **order in the
list *is* the priority**, which is both readable and safe to change.

**Cost:** No per-rule state or conditional composition. Not needed.

---

## 33. `--reset` on the filter stage

**Date:** 2026-08-11

**Decision:** `filter_all.py --reset` returns `filtered`/`rejected` rows to
`status='new'` and clears `role_family`/`reject_reason`. It never touches
`reviewed` or `applied`.

**Why:** Rule tuning is iterative and requires reclassifying the full corpus.
Used four times in the first session. Excluding `reviewed`/`applied` is the whole
point — my own decisions must survive a rule change, or I'd lose application
history every time I edited a regex.

**Cost:** None. This is what makes the stage safe to iterate on.

---

## 34. Store a reason for every rejection

**Date:** 2026-08-11

**Decision:** Every rejection writes a named `reject_reason`. Rules are named
after what they mean, not what they match (`customer_facing_eng`, not
`has_solutions_keyword`).

**Why:** The filter's failure mode is silently eating good jobs, and a filter you
can't audit is one you can't trust. This paid off immediately: reading the
`no_family_match` bucket revealed `developer infrastructure` and `test infra`
were missing from the platform pattern, and reading `location` revealed
`Remote | US or Canada` was being rejected. Neither would have been visible from
counts alone.

**Cost:** One column. Trivially worth it.

---

## 35. Reject rules run before family classification

**Date:** 2026-08-11

**Decision:** All `_REJECTS` are checked first; only survivors get a
`role_family`.

**Why:** No point spending classification work on something being discarded.
Also means a rejected row has `role_family = NULL` unless it was rejected *after*
classification (location, comp) — which is deliberate: those rows keep their
family so `reject_reason='location' AND role_family='platform'` is a meaningful
query.

**Cost:** Two rules (location, comp) need job data beyond the title, so they sit
after family matching. Slight asymmetry, worth it for the queryability.

---

## 36. `customer_eng` is a role family, not a rejection

**Date:** 2026-08-11 *(revises an earlier decision made the same day)*

**Decision:** Solutions Engineer, Forward Deployed Engineer, Customer Engineer,
Developer Advocate get `role_family='customer_eng'` and flow through the
pipeline as a separate review tier. Initially these were a reject rule.

**Why:** They *are* engineering roles — real technical work, often well paid. The
original rule said "not the engineering I want," which is a preference, not a
fact about the role. As a family they get extracted, scored, and reviewed
separately, which means real fit scores and gap lists rather than an opaque
reject bucket. Local extraction is free, so 56 extra jobs cost nothing.

**`customer_eng` is FIRST in `_FAMILIES`**, which matters: customer-facing is a
stronger signal than any product-area word. Without that ordering, "Senior
Customer Engineer — Cloudflare Developer Platform" tags as `platform`, which was
an observed false positive.

**Cost:** 56 jobs of extraction. Free at local-model prices.

---

## 37. `Platform` and `Infrastructure` cannot be matched as bare words

**Date:** 2026-08-11

**Decision:** The `platform` family matches specific phrases (`developer
productivity`, `developer infrastructure`, `test infra`, `build engineer`,
`release engineer`, `internal tool`, `platform productivity`) — never bare
`platform` or `infrastructure`.

**Why:** These are the most overloaded words in engineering titles. Observed in
real data: "Data Platform," "AI Platform," "Growth Platform," "Furnishing
Platform," "Identity & Authorization Platform," "Compute Platform." All product
engineering teams that happen to build platforms *for other product teams* — not
developer productivity work.

The distinction that matters: **is the customer an engineer at this company, or a
user of the product?**

**Cost:** Phrase lists need maintenance as titles evolve. Reading the
`no_family_match` bucket surfaces gaps.

---

## 38. Staff+ rejected as its own reason, not folded into seniority

**Date:** 2026-08-11

**Decision:** `seniority_staff` is a separate `reject_reason` from
`seniority_too_high`. 497 rows.

**Why:** "Staff" means different things at different sizes. At a 60-person
startup it's often "senior IC with scope" — a legitimate stretch for someone
targeting senior. At Databricks it screens hard on prior staff-level org impact.
That judgment can't be made from the title alone, so the rule rejects by default
but keeps the bucket queryable in one line. `Principal` and above stay in
`seniority_too_high` — those are genuine no's.

**Cost:** One extra reason string.

---

## 39. Location: an acceptable option anywhere in the string wins

**Date:** 2026-08-11

**Decision:** `location_ok()` checks for Portland or remote+US **before**
applying the foreign-location exclusion. Only if no acceptable option is found
does `_FOREIGN` reject.

**Alternatives considered:** Exclusion first (the original implementation).

**Why:** Location fields are frequently *lists*, not values. Real examples:
`"San Francisco, CA, New York, NY, Portland, OR, or Remote within Canada or
United States"` and `"Remote | US or Canada"`. Exclusion-first rejected both —
including one that literally names Portland — because the word "Canada" appeared.

Two other real bugs fixed here:

- `,\s*or\b` (intended to match `"Portland, OR"`) matched `", or NS Only"` in
  `"Canada - Remote (ON, AB, BC, or NS Only)"`. Dropped the state abbreviation
  entirely — no posting writes "OR" without also writing "Portland."
- Country tokens (`united states`, `us`) were originally in the *accept* list,
  which passed `"Denver, US"`. Country alone says nothing about commutability;
  it's now only meaningful in combination with a remote marker.

**Cost:** A string offering only foreign options that happens to contain "US" as
a substring could slip through. `\b` boundaries mitigate this.

---

## 40. Reject on location, but keep the rows visible

**Date:** 2026-08-11

**Decision:** Location-rejected jobs keep their `role_family`, so
`reject_reason='location' AND role_family='platform'` returns them.

**Why:** This surfaced a genuine market fact: 8 US/Canada DevProd roles were
rejected on location (SF, NYC, Toronto, Foster City) versus 7 that passed. **The
niche exists and clusters in metros** — a conclusion only visible because the
rejected rows kept their classification. Had they been discarded or left
untagged, the reasonable inference from `platform = 7` would have been "this
niche is tiny," which is wrong.

Practical value: if the queue ever feels thin, that bucket is where to look
before adding companies or relaxing rules. Preserves the relocation option
without acting on it.

**Cost:** None. The data was already there; this is about not throwing away the
tag.

---

## Also worth recording (not decisions, but measured facts)

**The ATS `remote` flag is unreliable.** Observed `remote = 1` on three postings
with location `San Francisco`. `location_ok()` no longer treats the flag as
sufficient on its own — it must be corroborated by an acceptable location token.
Third instance of the same lesson as decisions 10, 14, and 26: **verify what an
API actually returns rather than trusting the field name.**

**Duplicates are real and expected.** ClickHouse posted "QA Engineer - Core
Database" four times with distinct `source_job_id` values (one per location).
Correct behavior for `UNIQUE (source, source_job_id)` per decision 7, but it
inflates the review queue. A soft dedupe on `(company_id, title)` at review time
would collapse them. Backlogged.

**Measured filter output:** 7,186 → 232 (97% reduction). `swe` 146,
`customer_eng` 56, `sre` 19, `platform` 7, `sdet` 4. Tier 1 is small; `sre`
matters more than "tier 3" implies; the company list is the biggest available
lever on volume.


## Open questions

- Add a guard rail to closed detection: refuse to close more than ~30% of a
  company's jobs in one pass (protects against partial API responses).
- Plaid, Snyk, Deel, Expensify return 0 jobs from valid boards — probably moved
  boards. Worth 5 minutes for Plaid and Snyk.
- 15 companies still unresolved but potentially fixable (HashiCorp, Sourcegraph,
  Retool, dbt Labs, Fly.io, BrowserStack, ...). Low priority.
- Workday support — biggest coverage gap. Per-tenant POST to a JSON endpoint.
- No sanity check that a company's job count hasn't collapsed between runs.
- Greenhouse exposes no structured comp (0 of 5,650 rows), but many descriptions
  state ranges in text — the 1Password JD had "$113,000 USD and $158,000 USD"
  inline. A regex pass over `raw_json` would recover a meaningful chunk with no
  re-fetch needed.
- JD structure is a sandwich, not back-loaded: ~1,900 chars of company boilerplate,
  then the real content, then ~40% legal/benefits/EEO tail. Positional truncation
  would cut the requirements section; extraction needs section-boundary detection
  on start markers ("What we're looking for", "Requirements") and end markers
  ("Equal Opportunity", "What we offer", "The annual base salary"). Extract comp
  from the tail before discarding it.
