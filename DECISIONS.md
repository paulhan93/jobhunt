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

## 41. Section-boundary detection needed typographic-quote normalization

**Date:** 2026-08-16

**Decision:** Normalize curly quotes/dashes to ASCII (`str.translate`) before
matching section markers in `pipeline/extract.py`.

**Why:** Real bug, not theoretical. Marker matching used straight quotes
(`what you'll do`); real JDs almost always use curly ones (`what you’ll do`,
U+2019). With no marker match, the code fell through to an incidental
mid-sentence use of a reject word (`"...job responsibilities. We're extremely
proud..."`), producing a ~60-character garbage slice instead of the real
requirements section. Two unrelated jobs both hit this and got byte-for-byte
identical, generic model output — that's what made it visible. Checked all 247
filtered jobs after the fix; found and fixed one further miss the same way
(broadened the marker list for `what will you do` / `what we look for`
phrasing).

**Cost:** None found. Pure correctness fix.

---

## 42. Comp regex needed to handle per-number currency suffixes

**Date:** 2026-08-16

**Decision:** `_COMP_RANGE` now allows an optional `USD` after *either* number,
not just the second.

**Why:** The regex's own motivating example — the 1Password JD quoted in §7 of
PROJECT.md, `"$113,000 USD and $158,000 USD"` — has `USD` after each number.
The original pattern only handled `USD` at the end of the range (`"$X - $Y
USD"`) and silently returned no match on the exact phrasing that justified
building it in the first place. Caught by testing against the live posting
rather than trusting the design doc's example was already covered.

**Cost:** None. Backfilled comp on already-extracted rows once fixed.

---

## 43. Structured-output JSON schemas are not portable between Ollama and Claude

**Date:** 2026-08-16

**Decision:** Every object in a schema passed to `output_config.format` needs
`additionalProperties: false` explicit. Nullable enum fields use `anyOf:
[{type, enum}, {type: "null"}]`, not `"type": ["string", "null"], "enum": [...,
null]`.

**Alternatives considered:** None — this was a hard 400 from the API, not a
style choice.

**Why:** Ollama's `format` param tolerated both the missing
`additionalProperties` and the type-array-plus-null-in-enum pattern; Claude's
`output_config.format` rejects both outright. Found by testing the new Claude
provider path end-to-end before considering it done, not by inspection — the
same schema had been running fine on Ollama for the whole extraction backlog.

**Lesson:** A schema validated against one provider is not validated. If a
schema is meant to serve two backends, it needs a real call against both
before being trusted, same principle as decision 26 (verify a live response
before trusting a parser).

**Cost:** None. Re-verified Ollama still produces correct output after the fix
— it does; the extra schema constraints are additive, not behavior-changing.

---

## 44. Ollama stays the default; Claude is an escape hatch, not a migration

**Date:** 2026-08-16

**Decision:** `pipeline/extract.py` carries `PROVIDER = "ollama" | "claude"`
(default `"ollama"`) and `BATCH_THRESHOLD = 300` for an auto-switch to the
Claude Batch API on large runs.

**Alternatives considered:** Migrating extraction/matching to Claude outright,
given it's measurably more accurate on the same prompts; defaulting the batch
threshold to a low number like 60.

**Why:** Decision 24 already settled local-for-volume, cloud-for-quality — this
doesn't reverse that, it adds a deliberate lever for the case where the
tradeoff flips (a one-time quality-focused re-run, or the rare case where
per-call cost stops mattering). Batch's threshold went to 300, not 60, after
actually measuring both: this project's realistic volume (a few hundred jobs
at most) makes the Batch API's 50%-off discount worth low single-digit dollars
— financially irrelevant — while batch has a real latency floor (~90 seconds
minimum observed on a 2-request test batch, no guaranteed turnaround beyond
"usually under an hour") that can make it *slower* than just calling
sequentially. Batch only wins at volumes this project doesn't operate at.

**Cost:** Two provider integrations to keep in sync (see decision 43 for the
first real cost of that).

---

## 45. Long-running scripts commit per unit of work, not once at the end

**Date:** 2026-08-16

**Decision:** `scripts/extract_all.py` commits after every job, not once for
the whole run. `pipeline/db.py`'s `get_conn()` now passes `timeout=30` to
`sqlite3.connect()` instead of the 5s default.

**Why:** The first draft wrapped the entire multi-hour extraction run in one
`with get_conn() as conn:` block — nothing committed until the whole run
finished, so a crash near job 200 of 252 would silently roll back everything
already done. This directly violates the "every stage must be idempotent and
resumable" convention (PROJECT.md §9) even though the individual per-job work
already was idempotent; the transaction boundary undid that. Separately, once
multiple things legitimately hold `jobs.db` open at once (a long-running
`extract_all.py`, a manual query, Datasette), the sqlite3 module's 5-second
default busy-timeout fails fast with "database is locked" instead of just
waiting a beat — bumped to 30s.

**Cost:** More frequent commits are marginally slower in aggregate. Irrelevant
next to hours of model-call latency dominating the runtime anyway.

---

## 46. `fit_tier` thresholds are a starting guess, not derived from data

**Date:** 2026-08-16

**Decision:** `apply` at `fit_score >= 70`, `stretch` at `>= 40`, else `skip`.
Any unmet must-have failing its years requirement by more than 2x caps the
tier at `stretch` regardless of score.

**Why:** PROJECT.md §7c specifies the scoring formula and the years-cap rule,
but never pinned exact tier cutoffs. Chose round numbers as a starting point,
explicitly meant to be retuned the same way the filter rules were (decision
33's `--reset` pattern) once real scores exist to look at.

**Cost:** Early tier labels may be miscalibrated until retuned against real
review outcomes. Cheap to fix — it's two numbers in `pipeline/score.py`.

---

## 47. Bare `sales` in `not_engineering` was swallowing `customer_eng`'s own pattern

**Date:** 2026-08-16

**Decision:** Removed the bare `sales` keyword from `_REJECTS.not_engineering`.

**Why:** Same bug class as decisions 34/37, found the same way — reading a
reject bucket. `_FAMILIES.customer_eng` explicitly lists `sales engineer` as a
pattern it should catch, but `_REJECTS` runs first (decision 35) and its bare
`sales` keyword matched "Sales Engineer" before the family loop ever ran —
dead code. 61 rows were sitting in `not_engineering` that should have reached
family classification. Safe to remove because `_FAMILIES` is a strict
allowlist, not a blocklist (decision 32): anything not `sales engineer` still
has to match one of five patterns to survive, so a bare `Sales Manager` or
`Sales Rep` still correctly falls through to other reject rules or
`no_family_match`. Verified: 10 of the 61 now correctly pass as `customer_eng`,
44 correctly still reject on `location`, 10 correctly still reject as
`seniority_too_high` (Director/VP-level sales titles).

Same fix pattern applied when `tpm` was added: removed `product manager` from
`not_engineering`, and added `(?<!product )manager` to `seniority_too_high` so
"Product Manager" doesn't get caught by the bare `manager` reject that's meant
for actual people-managers.

**Cost:** None — strictly more correct, same as decision 10.

---

## 48. `ai_eng` and `tpm` added as role families; generalist PM stays out of scope

**Date:** 2026-08-16

**Decision:** Two new `role_family` values. `ai_eng` (AI/Applied AI/Agentic/LLM
Engineer — deliberately excludes generic "Machine Learning Engineer," which
means classic ML/data-science work, a different skillset). `tpm` (**Technical**
Product Manager specifically — title must say "Technical Product Manager" or
combine "Product Manager" with a technical/platform context word). Generalist
PM (roadmap/business ownership, no technical bent) stays explicitly out of
scope.

**Why:** `ai_eng` directly matches demonstrated project experience (the
`privew` multi-agent pipeline; this project's own model-does-extraction /
arithmetic-does-judgment design) and the stated preference for system design
over hands-on coding. `tpm` covers the "decide what to build for a
developer-facing system" niche, real but lower-priority given the
platform/DevProd focus elsewhere. Generalist PM was considered and rejected on
the merits — different job function, no overlap with `resume.yaml`'s bullet
bank, comp doesn't clear senior SWE except at big tech — not just left out by
default.

**Cost:** `_FAMILIES` ordering got one more constraint: `tpm` must be checked
before `platform`/`customer_eng`, or a title like "Technical Product Manager,
Developer Platform" would get stolen by the `platform` pattern on the word
"platform" alone.

---

## 49. Silent-failure bugs in extraction/scoring must fail loudly, not default to success

**Date:** 2026-08-16

**Decision:** Four related bugs fixed, all in the same failure family — code
that treated "no data" or "the request failed" as if it were a normal, good
result instead of raising or returning an explicit error state:

1. `score_job()` defaulted `must_hit`/`nice_hit` to `1.0` when a job had zero
   extracted requirements — meaning an extraction failure (bad JD, model
   hiccup, failed batch call) scored as `fit=100, tier='apply'`, the exact
   opposite of the "every point traces to a requirement → bullet edge"
   guarantee decision 46 depends on. Fixed: `score_job()` returns `(None,
   None)` on zero requirements; `score_all.py` marks the job `status='error'`
   instead of writing a fake score.
2. `extract_requirements_batch()`/`match_evidence_batch()` coalesced a failed
   batch request (`None` — errored/canceled/expired, per decision 45's
   per-unit-of-work reasoning) into `[]`/`{}` — indistinguishable from "the
   call succeeded and genuinely found nothing." That silently fed bug 1: a
   failed batch call → empty result → scored as a perfect match, with no
   error anywhere. Fixed: `None` is preserved through both functions.
3. `process_batch()`'s per-job result-writing loop had no exception handling,
   unlike the per-job loop in `main()` — one malformed response (schema drift,
   an unexpected null) would crash the whole batch run and abandon every other
   job in it, up to hundreds. Fixed: same try/except-and-continue pattern as
   the non-batch loop.
4. `call_claude()`/`_run_claude_batch()` read the response with an unguarded
   `next(b.text for b in response.content if b.type == "text")` — a model
   refusal (`stop_reason: "refusal"`, HTTP 200, empty/non-text content) raised
   a bare `StopIteration` instead of a clear, catchable error.

**Why this is one decision, not four bug reports:** all four share the same
root cause — code trusting that a response is well-formed instead of checking.
The project's own convention (`PROJECT.md` §9) already says "network functions
return verdicts, never raise" and "ambiguous outcomes get their own category" —
these bugs were violations of that convention that happened to compile and run
without ever being exercised by a failure case in testing.

**Cost:** None — strictly more correct. The one behavior change: a job that
previously would have silently scored 100/apply on an extraction failure now
correctly lands in `status='error'` and needs a retry, which is the point.

---

## 50. `not_engineering` split into hard/soft tiers — a single flat reject list can't tell "unambiguous job function" from "department name that also qualifies real engineering titles"

**Date:** 2026-08-16

**Decision:** `not_engineering` was one flat keyword list checked before family
matching (same bug class as decision 47's bare `sales`, but general — not just
one keyword). Verified against the full `rejected` table, not hand-picked
examples: titles like "Software Engineer, Finance Applications" and "Sales
Engineer (Customer Success)" were being silently killed because "Finance" and
"Customer Success" are also in the reject list, even though the title also
says a phrase the pipeline explicitly wants.

Split into two tiers. **Hard** (`account executive`, `account manager`,
`recruit`, `talent`, `counsel`, `hr`, `program manager`, `project manager`,
`analyst`, `partnership`, `business development`, `bdr`, `sdr`, `solutions
consultant`, `technical writer`, `data scientist`, `research scientist`) —
these are never ambiguous; a title saying "Account Executive" is that job
regardless of what else it mentions — checked before family matching, same
position as seniority. **Soft** (`marketing`, `customer success`, `support`,
`finance`, `accounting`, `legal`, `people`, `communications`, `content`,
`brand`, `design`, `designer`, `operations`, `revenue`) — department names
that can legitimately qualify a real engineering title — only reject when no
family already matched.

**A regression in the first version of this fix, caught before it shipped:**
the first attempt checked family match before *any* not_engineering check,
with no hard/soft split. That let "Enterprise Account Executive, Observability"
flip to `sre` and "Technical Account Executive" flip to `customer_eng`, because
those family patterns match bare product-area words (`observability`,
`technical account`) with no requirement that "Engineer" appear in the title
at all — a single-instance test case wouldn't have caught this, only running
the new logic against the full 6,929-row rejected set and diffing against the
old classification did. The hard tier fixes it: `account executive` is
excluded unconditionally, before family matching runs, so it can't be rescued
by an incidental substring match.

**Measured impact:** re-ran the full corpus. 2 jobs moved fully into the
review queue (one scored 87.5/apply on first pass). 703 other rows that stay
correctly rejected now carry an accurate `reject_reason` (mostly
`seniority_too_high`/`seniority_staff`) instead of the misleading
`not_engineering` — no queue-size change, but the audit trail decision 34 was
built for is now actually trustworthy for these rows.

**Related, not fixed:** `swe`'s pattern requires the literal phrase "software
engineer" (or `backend engineer`/`full stack`/`swe`), so bare "Frontend
Engineer" or "iOS Engineer" titles match no family at all and never reach the
hard/soft split above — there's no family match to rescue them with. This is
a different root cause (a family-pattern gap, not a reject-list ordering bug)
and is still open — see "Open questions."

**Cost:** None — strictly more correct, same shape as decisions 10 and 47.

---

## 51. Comp regex needed a currency-prefix branch, not just a currency-suffix one

**Date:** 2026-08-16

**Decision:** `_COMP_RANGE` only recognized `$` as an optional prefix and
`USD` as an optional suffix on each number (the shape needed to fix decision
42's 1Password case: `"$113,000 USD and $158,000 USD"`). Found by inspecting a
Grafana Labs posting in the live queue: `"USD 127,651  -  USD 203,867"` uses
`USD` as a **prefix**, with no `$` at all — a shape the regex had no branch
for, so it silently returned `None, None, None` even though the JD states pay
in plain text. Same bug class as decision 42 (a comp format the regex wasn't
built to handle), just the mirror image of it. Fixed by replacing the bare
`\$?` prefix with `(?:\$\s?|USD\s+)?` on both sides of the range, then
re-verified against both the 1Password and Grafana text so the fix for one
didn't regress the other — same lesson as decision 43's "test both, not just
one."

**Why this matters beyond one job:** a `NULL` comp field is indistinguishable
from "the JD doesn't state comp," which is the same silent-failure shape
decision 49 was written to eliminate — the regex returning nothing on a
recognizable format is a quiet miss, not a loud one.

**Measured impact:** checked every `scored`/`extracted`/`reviewed`/`applied`
row with `comp_min IS NULL` (46 rows) by re-running the fixed regex against
each description. Only 1 was recoverable — job 7193 (Grafana), backfilled
directly (`comp_min=127651, comp_max=203867, comp_currency=USD`). The other 45
genuinely have no comp text in the description; this was a one-format gap, not
a systemic one.

**Cost:** None — strictly more correct, same shape as decisions 10, 47, 50.

---

## 52. Snapshot `fit_score`/`fit_tier` onto `applications` at the moment of applying

**Date:** 2026-08-16

**Decision:** `jobs.fit_score`/`fit_tier` are live and mutable — re-running
`score_all.py` after a formula change (as happened later this same session,
twice) overwrites them. Without a snapshot, a future "did apply-tier jobs
convert better than stretch-tier?" query would silently join against
whatever the score happens to be *today*, not what it was when the
application was actually made — corrupting the one analysis this project's
outcome tracking (step 8/9) exists to enable. Added
`fit_score_at_application`/`fit_tier_at_application` to `applications`
(migration 005 + `schema.sql`), populated automatically by
`scripts/log_application.py` from the job's current score at insert time —
no extra manual input required.

**Note on `ALTER TABLE` and `CHECK`:** same caveat as decisions 1 and 3 —
SQLite's `ALTER TABLE ADD COLUMN` can't carry a `CHECK` constraint, so the
live DB lacks the `fit_tier_at_application IN (...)` check that `schema.sql`
has for a fresh database.

**Cost:** two nullable columns, zero added user effort (auto-populated).

---

## 53. `scripts/log_application.py` — the missing link between applying and knowing if scoring works

**Date:** 2026-08-16

**Decision:** `applications` had a real schema but no tooling to write to it —
manual `INSERT`s only. Built a thin script:
`python -m scripts.log_application <job_id> [--referral] [--notes]
[--resume-version]`. It inserts the row, snapshots
`fit_score_at_application`/`fit_tier_at_application` (decision 52), and
flips `jobs.status` to `'applied'` in one transaction — logging only to the
side table while leaving `jobs.status` at `'scored'` would make the job
invisible to the "already applied" check but still show up everywhere else
as if untouched.

**Why now:** confirmed live — the table had **zero rows** despite jobs
already sitting in `apply` tier, meaning step 8/9's entire feedback loop was
unreachable purely for lack of a way to write to it, not for lack of design.

**Cost:** none — small, contained script, no schema risk.

---

## 54. Years-cap silently skipped for any requirement without a `skill_key`

**Date:** 2026-08-16

**Decision:** `score_job()`'s deterministic years-cap
(`have < years_required / N` → force `stretch`) only ran when the
requirement carried a `skill_key`. A very common JD phrasing — "5+ years of
software engineering experience," stated generally, not tied to one skill in
the controlled vocabulary — has no `skill_key` to look up, so the check
`continue`'d past it entirely: zero enforcement, not lenient enforcement.
Fixed by adding `load_total_years()` (sums all `experience` role durations,
same shape as the existing `load_skill_years()`) and using it as the
fallback comparison basis when `skill_key` is null, instead of skipping.

**Verification, not assumption:** dry-ran the fix against all 244 then-scored
jobs before touching the database — **zero jobs changed tier.** This is the
correct and expected result, not a failed fix: the project's own years-cap is
deliberately lenient (only caps when you're short by more than the
threshold), and total professional years (~4.6) already clears every
general-phrasing requirement currently in the corpus under that threshold.
The fix closes a real structural gap for future/more-extreme postings (a
"10+ years" general requirement, say) without silently changing anything
that's already correct today.

**Cost:** none — pure arithmetic, no API calls, no observed regression.

---

## 55. Years-cap threshold tightened from "short by 2x" to "short by 1.5x"

**Date:** 2026-08-16

**Decision:** Paul's call, not a data-driven finding — tightened the
years-cap trigger from `have < required / 2` to `have < required / 1.5`.
Explicitly recorded as **a risk-tolerance dial, not a fact**: there's no
outcome data yet (zero logged applications with `heard_back`/`outcome` set)
to say which threshold is "correct," same epistemic status as the `fit_tier`
70/40 thresholds (decision 46).

**Measured impact:** dry-run against the full 244-job corpus — **exactly one
job** changed tier (a Senior PM/Infrastructure Observability posting, 75.0
`apply` → `stretch`). Whichever number gets picked here has small, bounded
stakes on the jobs actually in front of Paul today.

**How to actually validate this later:** once `heard_back`/`outcome` data
exists (step 8) and enough applications are logged, check whether
`stretch`-tier applications specifically flagged by the years-cap convert
meaningfully worse than `apply`-tier ones. If not, loosen back toward 2x. If
so, tighten further. Guessing more precision into this number before that
data exists isn't a useful lever — decision 52's snapshot columns exist
specifically to make this check possible later.

---

## 56. Removed JD section-trimming entirely, rather than patch the marker list again

**Date:** 2026-08-16

**Decision:** `extract_relevant_section()` (decision-era design: trim the JD
to the "sandwich middle" via start/end marker strings, §7) had a confirmed
failure mode — it matches a marker **anywhere it appears, including
mid-sentence prose**, and takes the earliest hit. Found via a deliberate
blind-verification pass: sampled 20 scored jobs, independently judged fit
against `resume.yaml` *before* looking at the stored score (to avoid
anchoring), then compared. Two confirmed catastrophic cases:

- **Job 1145** (ClickHouse): the marker `"requirements"` matched inside
  "...survived real customer **requirements** like custom roles..." — a
  bonus-points sentence near the *end* of the real qualifications section —
  discarding essentially the whole real section. Stored score: 0.0 `skip`.
- **Job 5584** (Snowflake): `"you have"` matched inside
  "**BONUS POINTS IF YOU HAVE**," skipping past the real header ("OUR IDEAL
  CANDIDATE WILL HAVE:", not in the marker list) entirely. Every real
  requirement was discarded; the model extracted fake "musts" from leftover
  confidentiality boilerplate instead. Stored score: 12.5 `skip`.

**Why removal, not a bigger marker list:** the extraction prompt already
instructs the model to ignore perks/benefits/team fluff, and `extract_comp()`
in this same file already runs on the *full, untrimmed* text specifically
because trimming would cut off comp numbers living in the tail — there was
already a working precedent in this exact file for "let the model handle
noise rather than pre-filtering it with regex." Enumerating more markers
fixes today's known failures and guarantees nothing about tomorrow's
phrasing; deleting the fragile stage removes the whole bug class. JD length
at this project's scale (a few thousand characters) is nowhere near a
concern for a modern model's context window, so the token-cost savings
trimming bought were real but small, traded against a stage that could
silently destroy the one section that actually matters.

**Verified before rollout, not assumed:** ran `extract_requirements()` on
both known-bad jobs against the *full* JD text — job 1145 went from a
gutted extraction to 13 real requirements (4+ yrs, Go/Rust/C++,
TypeScript/Python, auth protocols, distributed systems, API design); job
5584 to 12 real requirements (2-7 yrs, CS fundamentals, Java/Python/C++/SQL,
degree) matching what a human read of the actual JD finds, with the fake
confidentiality-boilerplate "musts" gone.

**Full corpus re-run (`extract_all.py --reset`, then `score_all.py`), same
day:** 244 jobs, **0 errors**, 3,723 `requirements` rows written. Tier
distribution moved from **40 apply / 125 stretch / 79 skip** to **54 apply /
144 stretch / 46 skip** — 14 more jobs correctly surfaced as `apply`, 33
fewer wrongly buried in `skip`. Job 1145 landed at 47.9 `stretch`, job 5584
at 77.5 `stretch` (both recovered from `skip`; exact numbers differ slightly
from the isolated 2-job test above because of decision 57's temperature bug,
found *because of* this discrepancy).

**Added `extract_all.py --reset`** (mirrors `filter_all.py`'s existing
`--reset`): deletes `requirements` rows and returns
`extracted`/`scored`/`reviewed`/`applied` jobs to `filtered` first, so a
preprocessing/prompt fix like this one can be re-run cleanly instead of via
ad hoc SQL.

**`extract_relevant_section()` and its marker lists are now dead code** —
left in `pipeline/extract.py` for now (unused, not deleted) pending a longer
period of confidence before removing them outright.

---

## 57. Evidence-matching over-generosity on vague requirements: confirmed, deliberately not fixed

**Date:** 2026-08-16

**Decision:** The same blind-verification pass (decision 56) also confirmed
that broad, unfalsifiable requirements ("ability to work independently and
collaboratively") still get matched to many bullets simultaneously — job
6839 (Security Engineer): one such requirement matched **12 of 29 bullets at
once**. This directly contradicts the assumption that re-running extraction
through Claude Haiku (decision 44) would fix the over-matching pattern
originally observed on the local Ollama model — it didn't, at least not for
this failure mode.

**Explicitly decided not to fix, on Paul's call, not an oversight:** JD
requirements are routinely inflated in a tough market, the tier system
(`apply`/`stretch`/`skip`) already exists precisely to absorb imperfect
matches rather than hard-gate on them, and a generous match costs at most a
few seconds of review — not a bad application. One nuance recorded for
later, not acted on now: if matching stays this generous everywhere, scores
compress toward each other and the ranking gets less useful for
distinguishing a great match from a mediocre one. Revisit only if the
apply-tier list stops feeling differentiated in practice.

---

## 58. Claude extraction/matching calls run at API-default temperature, not `temperature=0`

**Date:** 2026-08-16

**Decision:** `call_ollama()` explicitly sets `temperature: 0` for
determinism. `call_claude()` — the currently-active default provider
(`PROVIDER = "claude"`) — set no temperature at all, silently running at
Claude's API default instead. Found by code inspection while investigating
an unrelated question, then **confirmed with a live discrepancy, not just
theoretically**: job 1145 scored 58.3 in an isolated pre-rollout test and
47.9 in the full corpus re-run (decision 56) — identical code, identical
input, different output, purely from re-rolling the same non-deterministic
call. Both landings put the job in the right tier, so it didn't cost
anything *this* time, but it means "re-run extraction to verify a fix"
cannot reliably distinguish a real change from noise.

**Fixed in all three Claude call sites**, not just the obvious one: the
per-job path (`call_claude`) and both Batch API request builders
(`extract_requirements_batch`, `match_evidence_batch`) — the batch paths
build their own `MessageCreateParamsNonStreaming` directly rather than going
through `call_claude`, so the fix had to be applied in three places, not one.

**Verified, not assumed:** ran `extract_requirements()` twice on identical
input post-fix — byte-identical JSON output both times.

**Cost:** none — strictly more correct, same shape as decisions 10, 47, 50,
51.

---

## 59. Comp floor checked `comp_min`, rejecting ranges that clearly overlap the floor

**Date:** 2026-08-16

**Decision:** `classify()` rejected on `comp_min < COMP_FLOOR` alone. A range
like `108,000–145,000` has `comp_min` under the 130k floor even though the top
of the range clears it by 15k — the rule was rejecting on the bottom of the
range instead of asking whether the range *overlaps* the floor at all. Found
via a second Claude session reviewing the filter logic, verified against the
live table rather than taken on faith: of 24 rows tagged `comp_below_floor`,
only 4 actually carried comp data (the ATS coverage gap decision 51 already
documented — comp is sparse); of those 4, all 4 had `comp_max` clearing 130k
(e.g. job 460: 108k–145k; job 7358: 128k–184k) and were being wrongly rejected.

Fixed to check `comp_max`, falling back to `comp_min` when `comp_max` is null
(single-figure postings, or rows fetched before `comp_max` existed). Also had
to add `comp_max` to `filter_all.py`'s `SELECT` — it only pulled `comp_min`,
so the fixed `classify()` would have hit a `KeyError` on `job["comp_max"]` the
first time it ran against a real row. Caught before shipping by importing and
exercising the actual query, not just unit-testing `classify()` in isolation.

**Measured impact:** part of the combined 85-job re-run below (decisions
59–62 landed together, then the full corpus was reset and reclassified once).

**Cost:** none — strictly more correct, same shape as decisions 10, 47, 50,
51, 58.

---

## 60. `swe` broadened for bare "X Engineer" titles — explicit allowlist, not a bare `\bengineer\b` catch

**Date:** 2026-08-16

**Decision:** Closes the gap decision 50 flagged as "related, not fixed":
`swe`'s pattern required the literal phrase "software engineer" (or `backend
engineer`/`full stack`/`swe`), so a bare "Frontend Engineer" or "iOS Engineer"
title matched no family at all and silently fell through to
`no_family_match` — there was no family match for the hard/soft
`not_engineering` split to protect, because the loop that assigns family never
fired.

**First candidate tested and rejected:** a bare `\bengineer\b` fallback
(assign `swe` whenever no other family matched, no soft-reject fired, and the
title contains the word "Engineer" at all). Tested against the full
`no_family_match`/`not_engineering` set before shipping, not assumed correct —
it flipped **214** titles, and most of them were exactly the noise the
project's own backlog note worried about: `IT Systems Engineer`, `Field
Engineer`, `Consulting Engineer`, `Professional Services Engineer`, `Firmware
Engineer`, `Detection Engineer`, `Red Team Engineer`, `GTM Engineer`, `Deal
Desk Engineer`. Discarded.

**Shipped instead:** an explicit allowlist covering only the specialties
actually wanted — `frontend`, `front-end`, `product`, `distributed systems`,
`android`, `ios`, `mobile`, `analytics`, `api` — engineer. Re-tested against
the same full set: **64** titles flip, all genuine engineering titles (Android/
iOS/Mobile Engineer, Frontend Engineer, Product Engineer, Analytics Engineer,
Distributed Systems Engineer, API Engineer variants). `ml`/`security`/
`network`/`grc`-flavored titles stay excluded because they never match any of
the new phrases, not because of an explicit exclude list — narrower is safer
here than trying to enumerate every specialty to exclude.

**Side effect, not the intent:** two titles that were being wrongly rejected
by the still-open `design` bare-keyword bug (`Senior Frontend Engineer -
Design Systems`, `Senior Android Engineer, Design System` — see decision 50's
"related, not fixed" note and the backlog item of the same name) now pass,
because the broadened family match fires before the soft `not_engineering`
check ever runs. This fixes those two titles but not the underlying bug — a
title that matches *only* on "design" and nothing in the new allowlist would
still be wrongly rejected. The `design` bare-keyword bug is still open.

**Measured impact:** part of the combined 85-job re-run below.

**Cost:** none — strictly more correct. Chose the narrower allowlist over the
broader catch-all specifically to avoid the cost the broad version would have
had: diluting `swe`'s "wide net, lower conversion" family with roles (IT,
consulting, professional services) that aren't engineering at all.

---

## 61. Bare `management` in `seniority_too_high` was a domain-noun false positive, not a people-manager signal

**Date:** 2026-08-16

**Decision:** `seniority_too_high` rejected on bare `management` anywhere in
the title, not just `manager`. Domain terms like "Identity & Access
Management" or "Vulnerability Management" contain the word but don't make the
role a people-manager position — e.g. "Senior Software Engineer - Identity &
Access Management" was being rejected as too-senior, when it's a plain senior
IC engineering title that happens to name the system it's built for. Same
shape as decisions 47/50 (a keyword firing on a domain noun instead of a job
function) but in the seniority reject list instead of `not_engineering`.

Removed the bare `management` alternative; `(?<!product )manager` (already
present) still catches genuine people-manager titles ("Engineering Manager",
"Manager, Platform"). **Verified this doesn't open a hole**: checked all 64
titles in the live table that were rejecting on `management` with no
`manager` word present. The ones with real seniority signal elsewhere
(`director`, `head of`, `vp`, `principal`, ...) still reject on that signal,
unchanged. The ones with zero other engineering or seniority signal
(`Workforce Management Lead, Enterprise Ops`, `Third Party Risk Management and
Customer Trust Lead`) still end up rejected — just via `no_family_match`
instead of `seniority_too_high` now, since nothing in the title matches any
family pattern either. Only titles that also carry a real family signal
(`Engineer`, `Architect`, etc.) get rescued, which is the intended effect.

**Measured impact:** part of the combined 85-job re-run below.

**Cost:** none — strictly more correct, same shape as decisions 10, 47, 50,
51, 58, 59.

---

## 62. Solutions Architect exempted from `seniority_too_high`'s `architect` keyword, folded into `customer_eng`

**Date:** 2026-08-16

**Decision:** `customer_eng`'s family pattern already included `solutions?
architect` (it's presales/customer-facing work, same track as Sales Engineer)
— but `seniority_too_high`'s bare `architect` keyword ran in `_REJECTS`,
*before* family matching ever executes, so every Solutions Architect title was
rejected as too-senior before the customer_eng pattern got a chance to claim
it. The family-list entry was dead code for this specific title.

Confirmed with Paul this is wanted: Solutions Architect stays folded into
`customer_eng` — same reasoning as Sales Engineer, lowest priority of the
7 families, but should stay visible rather than silently discarded. Fixed
with the same exception shape as the existing `(?<!product )manager` carve-out:
`(?<!solutions )(?<!solution )architect`. Plain `Architect` / `Enterprise
Architect` / `Software Architect` — genuinely a more-senior-than-target title
with no presales angle — still reject via `seniority_too_high`, unchanged.

**Measured impact:** by far the largest single contributor to the 85-job
re-run below — 53 of the 85 newly-passing jobs are `customer_eng`, nearly all
of them Solutions Architect variants (Databricks, Salesforce-style regional/
vertical SA postings). This is expected and intentional per Paul's call, not
a bug — `customer_eng` review should stay selective (tier 7 of 7, "review
mainly when the queue is thin" per `PROJECT.md` §2), the fix just makes the
pool it draws from accurate instead of silently empty.

**Cost:** none — strictly more correct. Flag for review time, not a fix
concern: this family will now contain a lot of near-duplicate regional/
vertical Solutions Architect postings from the same few companies (see the
`Also worth recording` dedupe note) — worth the standing soft-dedupe-on-title
backlog item more than it was before this fix.

---

## 63. Claude structured-output rejects schemas with more than 24 optional properties — `tailor.py`'s `reword` field needed an array shape, not one property per bullet id

**Date:** 2026-08-16

**Decision:** Step 7 (`pipeline/tailor.py`, PROJECT.md §8) needed a JSON
schema constraining the model's `reword` output to the resume bank's own
bullet IDs — same "model structurally cannot invent an ID" mitigation as
`match_evidence`'s `bullet_ids` enum (§7b). The first version modeled
`reword` as an object with one optional string property per bullet ID (29 of
them, one per key in the bank). Calling the real API (not assumed to work
because the shape looked reasonable) returned a 400: `"Schemas contains too
many optional parameters (29), which would make grammar compilation
inefficient... limit: 24."` The resume bank was already one bullet over the
limit on the very first real call.

Fixed by switching `reword` to an array of `{id, text}` objects — the same
shape `_build_matching_schema`'s `matches` field already uses in
`pipeline/extract.py` for an equivalent id-keyed mapping, so this isn't a new
pattern, just one that should have been reused from the start. Converted
back to a `{id: text}` dict in `tailor_resume()` immediately after the API
call, so `TailoredResume.reword` and everything downstream (`build_resume_doc`,
`reword_diffs`) still works with the plain-dict shape the PROJECT.md §8 spec
describes — the array is a wire-format detail, not a design change.

**Why this matters beyond one field:** the resume bank only needs to grow
past 24 bullets (it's already at 29, and §8 explicitly wants ~8 bullets per
role across a bank meant to be 3-4x resume size) for the same failure to hit
any other bullet-id-keyed object schema. Any future schema shaped "one
optional property per resume-bank ID" should default to the array-of-objects
form instead.

**Cost:** none — strictly more correct, and cheaper to fix now (one field,
caught before the feature shipped) than after the bank grows further.

---

## 64. `tailor.py`'s reword instruction let the model pad bullets with unearned interpretive framing

**Date:** 2026-08-16

**Decision:** The first version of the reword prompt said "same facts,
numbers, technologies, and scope as the original... never add a claim." Run
against a real scored job (3775, Honeycomb Senior PM–Platform, live Claude
call, not a hypothetical), the model reworded bullets by appending clauses
like `"...on schedule ahead of the ZS11 launch — demonstrating ability to
establish clarity and ship complex technical work through ambiguity"` and
`"...driving data-informed prioritization and cross-functional alignment at
scale."` No fact, number, or employer was invented — the failure mode §8
explicitly guards against ("never let a model regenerate employment history
from prose") didn't happen — but this is still scope creep past "tightened
phrasing": editorializing commentary about what the bullet *demonstrates*,
which isn't in the original text and reads as resume-speak padding rather
than the candidate's own claim.

Tightened the instruction: explicitly named the failure pattern ("do not
append interpretive framing... if you're not removing or reordering words
from the original, it isn't a tightening") and added "most bullets shouldn't
need rewording at all" to push back on the model defaulting to rewording
every selected bullet. Re-ran against the same job immediately after: all
three rewrites became genuine trims (removing "a", "to follow", minor
restructuring) with no added framing.

**This is exactly why §8 requires a diff before anything ships** — the
system caught its own bug via the mechanism designed for it
(`reword_diffs()`, printed by `scripts/tailor.py` before rendering), not by
accident. Recorded as a decision anyway, rather than leaving it to be
silently caught by the diff step every time, because a bug worth catching by
review is a bug worth reducing at the source.

**Cost:** none — strictly more correct, and the fix is prompt wording, not
new validation logic. The diff-before-shipping mitigation stays in place
regardless — this reduces how often a human needs to actually edit the
output, it doesn't replace reviewing it.

---

## 65. Nothing checked rendered page count — "compiled successfully" and "fits on one page" were silently treated as the same thing

**Date:** 2026-08-16

**Decision:** `render_resume()` selected 8-12 bullets as a length proxy
(§8's tailoring prompt) and called `typst compile`, but nothing ever looked
at the actual output. Confirmed by testing directly: `typst compile` exits 0
regardless of page count — an 11-page stress-test document compiled with no
error, no warning, nothing. A job where the model picked bullets on the
higher end of its 8-12 range, plus a summary, plus two projects, could have
silently shipped as a 2-page PDF and nothing in the pipeline would have
known.

Fixed by adding `pypdf` (chosen because there's no `pdfinfo`/poppler on this
machine, and a hand-rolled page-count parser would have to handle Typst's
compressed PDF object streams — not worth it for one integer) and a
`_STYLE_LEVELS` shrink sequence in `pipeline/render.py`: compile at the
baseline style (10pt/1.8cm), check the real page count, and if it's over 1,
step through progressively tighter-but-still-readable levels (down to
9pt/1.4cm) until one fits or the levels run out. Deliberately does not drop
content or re-call the model to pick fewer bullets to force a fit — Claude
calls here run at `temperature=0` (decision 58), so re-running the exact
same selection call would just reproduce the same 15 bullets; the only way
to actually reduce content is either a different model call (cost, and not
guaranteed to converge) or an arbitrary drop rule (risks cutting something
that matters, which is exactly the kind of judgment call §8 reserves for the
human — "Assisted: LLM drafts, human approves"). `render_resume()` now
returns `(pdf_path, page_count)`; `scripts/tailor.py` prints an explicit
warning if the final page count is still over 1 rather than silently
shipping it as if it were fine.

**Verified against three real cases, not just the design:** a normal
7-bullet selection stayed at the baseline style (1 page, no unnecessary
shrinking); a realistic "model went over budget" 15-bullet selection
overflowed at baseline and was rescued by the shrink levels (1 page,
still legible — checked visually, not just by page count); an intentionally
extreme case (all 29 bank bullets, ~2.5x the prompt's own upper bound)
correctly stayed reported as 2 pages after exhausting every level, rather
than being forced to 1 page at an unreadable font size.

**Cost:** one new dependency (`pypdf`, pure Python, no compiled
extensions) and a handful of Typst recompiles in the rare case a resume
needs the shrink levels — cheap relative to shipping an oversized resume
unnoticed.

---

## 66. Comp floor never re-checked after `extract_all.py` backfills comp — Greenhouse jobs can pass the filter gate with unknown comp, then get a below-floor number attached one stage later with nothing watching

**Date:** 2026-08-16

**Decision:** Found by Paul spotting a live example in the datasette queue,
not by audit: job 3434 (GitLab, "Senior AI Engineer," `apply`/95.8) had
`comp_max = 129,600` — under the 130k floor `filters.py` is supposed to
enforce. Root cause: comp data arrives in two separate waves that the
pipeline never reconciles. `filter_all.py`'s comp check only sees whatever
`comp_min`/`comp_max` exist at fetch time — and per §5, Greenhouse never
exposes comp structurally *at all*, Ashby only ~47% of the time. For those
jobs, comp is `NULL` at filter time, the floor check correctly has nothing
to check (§6's "bias toward passing when evidence is absent" is the right
call *at that moment*), and the job passes. The real number often only
surfaces later, when `extract_all.py`'s `extract_comp()` regex-scans the JD
text and backfills `comp_min`/`comp_max` onto the row — but nothing
re-applies the floor after that backfill. The gate had already closed.

This is a different bug from decision 59 (which fixed *what* the floor check
compares — `comp_max` vs `comp_min`) — this is about *when* the check runs
relative to when the data actually exists. Decision 59's fix is still
correct and necessary; it just can't help a job whose comp was unknown at
the moment it ran.

**Fix:** `_fails_comp_floor()` in `scripts/extract_all.py`, called
immediately after `extract_comp()` in both `process_job()` and
`process_batch()`, before either one spends a model call on
extraction/matching. Only re-checks comp that's actually new
(`job["comp_min"] is None`) — comp already present at filter time was
already correctly evaluated then, under whichever version of the logic was
live at the time, and re-checking it here would be redundant. A newly-failing
job is rejected immediately (`status='rejected'`,
`reject_reason='comp_below_floor'`) instead of proceeding to extraction — in
the batch path this also removes it from the batch request before
submission, not just after, so a job that's about to be rejected anyway
doesn't cost a model call first. `process_job()` now returns a verdict
(`"extracted"` / `"comp_rejected"`) instead of `None` so the caller can log
and count the two outcomes separately, rather than the console printing
"ok" for a job that was actually rejected before any extraction happened.

**Measured scope, checked against the live data, not assumed to be just the
one job Paul found:** exactly 2 jobs currently affected — 3434 (Greenhouse,
comp only ever knowable via regex) and 7527 (Ashby, this specific posting
just didn't have structured comp even though Ashby sometimes does). Both
corrected: `requirements` rows deleted (29 total — wasted extraction cost
already spent, can't be recovered, but the rows shouldn't exist for a
rejected job), `status`/`reject_reason` set, `fit_score`/`fit_tier` cleared.
Re-ran the audit query after fixing: 0 remaining violations. Corpus count
adjusted: 244 → 242 scored (53 apply / 143 stretch / 46 skip, down from 54 /
144 / 46).

**Why this matters more going forward than the 2-job count suggests:**
Greenhouse is 40 of 70 companies — by far the largest single ATS segment —
and its comp data can *only* ever arrive via this backfill path, never at
fetch time. Every future `extract_all.py` run will keep hitting this unless
the check is at the point where the data actually becomes known, which is
what this fix does.

**Cost:** none — strictly more correct, and the batch-mode change is a net
cost *saving* (fewer wasted model calls on jobs that would be rejected
anyway).

---

## 67. `extract_all.py --job-id`, and a no-op reword filter — both surfaced by actually applying to a real job

**Date:** 2026-08-16

**Decision, part one:** Paul chose to apply to job 3434 (GitLab, "Senior AI
Engineer") anyway, deliberately, despite decision 66 having just rejected it
for comp $400 under the floor — his floor is a preference he can override,
not a hard rule the tool should enforce on his behalf. Reverting the
rejection was one UPDATE, but re-extracting it hit a real gap:
`extract_all.py` had no way to process one specific job. Its only query was
"everything at `status='filtered'`" — running it plain would have also
pulled in the 85 jobs rescued by yesterday's filter fixes, which Paul had
explicitly said to leave for later (real API cost, not free). Added
`--job-id` so a single manual case like this doesn't force processing
everything else sitting in the same status. Verified live: ran
`--job-id 3434`, confirmed via a status-count query that only that one job
moved out of `filtered` and the other 85 were untouched.

**Decision, part two:** `reword_diffs()` printed a "diff" for bullet
`privew-1` with byte-identical before/after text — the model included it in
`reword` without actually changing it. Harmless to the rendered resume
(`build_resume_doc()`'s `.get(id, original)` just returns the same text
either way), but it wastes the human reviewer's attention on a change that
isn't one, undermining the exact review step decision 64 was built to keep
meaningful. Fixed by excluding whitespace-normalized-identical entries from
`reword_diffs()`'s output. Verified against the real `reword` payload from
this run: 2 entries in, 1 real diff out.

**Both found by actually using the tool for its real purpose, not by
auditing.** Consistent with the pattern all session: real bugs surface
fastest by running the thing against a real job, not by reading the code
and guessing what might go wrong.

**Cost:** none — `--job-id` is additive (no existing call site changes
behavior without the flag), and the reword filter only removes noise, never
a real diff.

---

## 68. Explicit per-role bullet-count rules — the anchor role (most recent) always gets 4-6, everything else caps at 2-4

**Date:** 2026-08-16

**Decision:** Reviewing the first two real tailored resumes, Paul flagged
that Oracle — his most recent, most relevant role — was only getting 1
bullet, no better represented than 4G Clinical (a part-time internship from
2021). The original prompt only said "8-12 bullets total, prefer fewer,
stronger" with zero awareness of which role should carry the most weight.

Fixed with the same two-layer pattern used everywhere else in this file: a
clear prompt instruction (per-role targets, with the anchor role — always
`resume["experience"][0]`, not hardcoded to "Oracle" so this keeps working
the day that's no longer true — named explicitly by company/title) *and* a
deterministic enforcement pass, `enforce_bullet_counts()`, that runs after
the model responds and actually guarantees the anchor role lands in
`[ANCHOR_MIN_BULLETS, ANCHOR_MAX_BULLETS]` = `[4, 6]` and every other
role/project stays at or under `OTHER_MAX_BULLETS` = `4`. A prompted count
is a request, not a guarantee — same reasoning as the schema-enum-plus-
Python-recheck pattern already in `tailor_resume()` for bullet/skill IDs.
Trimming and topping-up both use each section's own `resume.yaml` bullet
order (the only ordering signal available, since `selected_bullets` doesn't
carry a priority ranking) — trim from the end of that order when over the
max, add the next not-yet-selected bullets in that order when under the
anchor's min.

**Verified before spending an API call:** five unit-test cases against the
real bank (under-selected anchor, 1→4; over-selected anchor, 17→6;
in-range anchor, 5→5 unchanged; over-selected project, 5→4; unselected
non-anchor role stays at 0, not forced up) all matched expectations. Then
verified live against job 3434: Oracle landed at exactly 4, every other
section at 2-3, all within the rules, both by inspecting the rendered
`.typ` output directly and the PDF.

**Cost:** none — strictly more correct, and the anchor-role targeting fixes
a real quality gap (most-relevant experience under-represented) rather than
just adding a constraint for its own sake.

---

## Also worth recording (not decisions, but measured facts)

**Combined re-run after decisions 59–62 (comp floor, `swe` broadening, bare
`management`, Solutions Architect), 2026-08-16.** All four fixes were tested
individually against the full `rejected` table before being combined, then
the full corpus was reset (`filter_all.py --reset`, 6,937 rows: everything
previously `filtered`/`rejected`; the 244 already-`scored` jobs are a
different status and untouched) and reclassified in one pass. **0
regressions** — checked every row that was previously `PASSED` under the old
rules to confirm none of the four fixes flipped it to rejected; none did.
**85 jobs newly pass**: `customer_eng` +53 (mostly Solutions Architect, see
decision 62), `swe` +28 (mostly the broadened bare-Engineer titles, decision
60, plus a handful rescued by the comp-max and bare-management fixes), `sdet`
+3, `sre` +1. Total reviewable pool: 244 already-scored + 85 newly-filtered =
329. None of the 85 are extracted/scored yet — they're sitting at `filtered`,
awaiting `extract_all.py`/`score_all.py` same as any other filtered job.

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

**Measured filter output, after the full corpus reset and re-run (2026-08-16):**
7,181 → 244. `swe` 138, `customer_eng` 67, `sre` 19, `tpm` 7, `platform` 6,
`ai_eng` 6, `sdet` 1. Tier 1 is still small (platform + sdet = 7); `sre` is
explicitly lower priority now (`PROJECT.md` §2, 2026-08-16) — don't chase it
for volume, that framing is retired. The company list is still the biggest
available lever on the platform/sdet number specifically.

**Extraction, full corpus run via Claude Batch (2026-08-16):** 3,584 +
29 `requirements` rows across 244 jobs, **0 errors**, using the `PROVIDER =
"claude"` path (decision 44) with `BATCH_THRESHOLD` temporarily dropped to 200
for this one run so 242 filtered jobs actually cleared true batch mode instead
of falling back to per-job calls, then reverted to 300 after. Scored: 40 apply
/ 125 stretch / 79 skip. Supersedes the earlier 234-job/229-extracted/5-scored
Ollama-path run noted below, which is kept for the record of what surfaced
decisions 41/42.

**Extraction backlog, first real run (superseded above):** 2,914 `requirements`
rows written across 234 jobs (229 extracted, 5 scored) with **0 errors**, using
the local Ollama path. Two real bugs found and fixed mid-run (decisions 41,
42) — neither would have surfaced without testing against live JD text rather
than trusting the design as originally specced.

**Structured-output quality — resolved by decision 57, not just anecdotal
anymore.** The over-matching pattern first observed on the 3B Ollama model
was confirmed to persist on Claude Haiku too, at measured scale (blind
20-job sample), and deliberately left unfixed — see decision 57 for the
reasoning.

**Extraction + scoring, full corpus re-run after removing JD section-trimming
(2026-08-16, see decision 56):** 244 jobs, 0 errors, 3,723 `requirements`
rows. Tier distribution: **54 apply / 144 stretch / 46 skip** (up from 40 /
125 / 79 before the trim-removal, years-cap, and threshold fixes landed the
same day). Supersedes the Batch-run numbers below, which are kept for the
record of what surfaced decisions 51's Grafana comp case and the original
(pre-56) tier counts.


## Open questions

- Add a guard rail to closed detection: refuse to close more than ~30% of a
  company's jobs in one pass (protects against partial API responses).
- Plaid, Snyk, Deel, Expensify return 0 jobs from valid boards — probably moved
  boards. Worth 5 minutes for Plaid and Snyk.
- 15 companies still unresolved but potentially fixable (HashiCorp, Sourcegraph,
  Retool, dbt Labs, Fly.io, BrowserStack, ...). Low priority.
- Workday support — biggest coverage gap. Per-tenant POST to a JSON endpoint.
- No sanity check that a company's job count hasn't collapsed between runs.
- ~~Same bare-keyword bug class as decision 47, found but not yet fixed~~ —
  fixed generally by decision 50's hard/soft split. The specific example
  ("Frontend Engineer - Design Systems") is *still* unmatched, but for a
  different reason: `swe`'s pattern doesn't recognize a bare "Frontend
  Engineer" title at all, so there's no family match for the hard/soft split
  to protect. Worth deciding how far to broaden `swe`'s pattern (bare
  "Frontend Engineer"/"iOS Engineer"/"Android Engineer"?) without pulling in
  noise — not done yet.
- 482 jobs sit permanently in `status='new'` — these are closed postings
  (`closed_at IS NOT NULL`), which `filter_all.py`'s query always excludes.
  Not a backlog to clear; this is expected steady state, not stale data.
