# Commands

Every entry point in this project, in the order you'd actually run them, with
what each one does, its flags, and a real example. `README.md` is the
orientation for a cold reader; `PROJECT.md` is the architecture and the "why."
This file is the "how do I actually run it" reference, kept separate so the
other two don't turn into command dumps.

Run everything from the repo root, as a module. `python scripts/foo.py` will
fail on imports (`scripts/` isn't meant to be run as a script directly, see
`PROJECT.md` §9); `python -m scripts.foo` is correct.

## Setup, once

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # runtime deps only
pip install -r requirements-dev.txt      # + pytest, for running tests

./reset.sh                               # DESTRUCTIVE: drops jobs.db and
                                          # rebuilds it empty from schema.sql.
                                          # Only for a fresh setup or a
                                          # deliberate full reset, never run
                                          # this against a database you care
                                          # about without a backup first.
```

## The pipeline, in order

### 1. `python -m scripts.probe`

Resolves each company in `personal/companies.txt` to an ATS + slug by trying
plausible slug guesses against all five ATS endpoints. Checkpoints to
`personal/probe_results.json` after every company, so it's safe to interrupt
and re-run, already-resolved companies are skipped. No flags.

```bash
python -m scripts.probe
#   HIT   Stripe: greenhouse/stripe (214 jobs)
#   MISS  SomeSmallCo
# 71/101 resolved
```

### 2. `python -m scripts.load_companies`

Reads `personal/probe_results.json` and upserts every resolved (`hit` or `empty`)
company into the `companies` table. No flags, safe to re-run.

```bash
python -m scripts.load_companies
```

### 3. `python -m scripts.fetch_all`

Fetches every active company's board, upserts postings into `jobs`, and runs
closed-detection (marks postings no longer returned as `closed_at`). This is
the one that runs on cron every 6 hours. No flags.

```bash
python -m scripts.fetch_all
#   Stripe                    214 jobs  +3 new
#   Coinbase                   89 jobs  +1 new  -2 closed
```

### 4. `python -m scripts.filter_all [--reset]`

Rules only, no model. Classifies every `status='new'` job as `filtered`
(passed, tagged with a `role_family`) or `rejected` (with a `reject_reason`).

- `--reset`: returns every `filtered`/`rejected` job back to `new` so the
  rules can be retuned and re-run against real data. Never touches `reviewed`
  or `applied`. Use this after editing `pipeline/filters.py`.

```bash
python -m scripts.filter_all
python -m scripts.filter_all --reset      # after changing a filter rule
```

### 5. `python -m scripts.extract_all [--limit N] [--job-id ID] [--reset]`

Runs the model over every `status='filtered'` job: extracts requirements,
matches them against the resume bank, writes to `requirements`, flips status
to `extracted`. Costs API calls if `PROVIDER = "claude"` in
`pipeline/extract.py` (currently the default here); free and local if set to
`"ollama"`. Automatically switches to the Batch API above `BATCH_THRESHOLD`
jobs in one run.

- `--limit N`: process at most N jobs. Use for a dry run before committing to
  a full pass, especially after changing the extraction prompt.
- `--job-id ID`: process exactly one job (must already be `filtered`). For a
  manual one-off, e.g. re-running a single job without pulling in the whole
  queue.
- `--reset`: deletes existing `requirements` rows and returns
  `extracted`/`scored`/`reviewed`/`applied` jobs back to `filtered`. Use this
  after a change to the extraction prompt or JD preprocessing, since the old
  extracted data is no longer trustworthy. Costs a full re-run of model calls.

```bash
python -m scripts.extract_all --limit 5           # dry run
python -m scripts.extract_all                      # full run
python -m scripts.extract_all --job-id 6748         # one specific job
```

### 6. `python -m scripts.rematch_all [--limit N] [--job-id ID]`

Re-runs *only* the evidence-matching step (not extraction) for any
requirement row with `match_strength IS NULL`. Cheaper than
`extract_all --reset` when only the matching logic changed, not what gets
extracted. Idempotent: only touches unmatched rows, safe to re-run.

```bash
python -m scripts.rematch_all                       # everything unmatched
python -m scripts.rematch_all --limit 10             # small sample first
```

### 7. `python -m scripts.backfill_comp [--limit N]`

Regex-only (no model, no cost). Re-runs `extract_comp()` against every
already-stored job description and fills `comp_min`/`comp_max` for any job
that's still `NULL`. Never overwrites an existing value, skips `rejected`
jobs. Use this after fixing the comp regex, to catch jobs processed before
the fix without burning a model call to re-extract everything.

```bash
python -m scripts.backfill_comp
```

### 8. `python -m scripts.score_all [--limit N] [--reset]`

Pure arithmetic, no model, no API cost. Computes `fit_score`/`fit_tier` for
every `status='extracted'` job from its `requirements` rows, flips status to
`scored`.

- `--limit N`: score at most N jobs.
- `--reset`: returns `scored`/`reviewed`/`applied` jobs back to `extracted` so
  they re-score against their *existing* requirements. Use this after a
  change to `pipeline/score.py`'s formula or thresholds, no API calls, no
  re-extraction needed.

```bash
python -m scripts.score_all
python -m scripts.score_all --reset          # after a scoring formula change
```

### Review: `datasette serve jobs.db`

Not a script, opens a local web UI over the SQLite file. The `review_queue`
view (`/jobs/review_queue`) is the sorted, filterable queue: fit score/tier,
company, comp, location, an `applied` flag. This is the human decision step,
nothing here is automated.

```bash
datasette serve jobs.db
# then open http://127.0.0.1:8001/jobs/review_queue
```

### 9. `python -m scripts.tailor <job_id> [--out DIR]`

One Claude call, tailors a resume for one specific job: selects bullets,
writes a summary, renders a PDF via Typst. `job_id` must already be scored.
Prints any reworded bullets as a diff, read it before sending, the model has
padded reworded text with unearned claims before (see `DECISIONS.md` #64,
#74, #76).

- `--out DIR`: output directory, defaults to `output/resumes`.

```bash
python -m scripts.tailor 6748
python -m scripts.tailor 6748 --out ~/Desktop/applications
```

### 10. `python -m scripts.log_application <job_id> [--resume-version V] [--referral R] [--notes N]`

Records that you actually sent the application: inserts into `applications`,
snapshots `fit_score`/`fit_tier` at that moment (so a later re-score can't
rewrite history), flips `jobs.status` to `applied`. Run this *after* you've
actually submitted, not before.

- `--resume-version`: the resume PDF's basename (printed by `scripts.tailor`
  when it finishes), so you know which exact tailored version was sent.
- `--referral`: who referred you, if anyone. Worth checking before every
  application, per `PROJECT.md` §11, referrals convert far better than cold
  applications.
- `--notes`: anything else worth remembering about this application.

```bash
python -m scripts.log_application 6748 \
    --resume-version paul-han-resume-supabase \
    --referral "Jane D. (ex-coworker, now at Supabase)"
```

## Testing

```bash
.venv/bin/python -m pytest              # all tests, ~0.1s, 100% local
.venv/bin/python -m pytest -v           # verbose, one line per test
.venv/bin/python -m pytest tests/test_filters.py    # one file only
```

Every test is pure: no network call, no model call, no API cost, and none of
them touch the real `jobs.db`. Safe to run any time, as often as you want.
Add a regression test here (see `DECISIONS.md` #77 for the convention) any
time a real bug in `classify()`, `score_job()`, or `extract_comp()` gets
found and fixed.

## After changing code

```bash
.venv/bin/python -c "import pipeline.filters"   # syntax-check one module,
                                                  # one second, versus finding
                                                  # an IndentationError four
                                                  # minutes into a real run
.venv/bin/python -m pytest                       # then the real test suite
```

## Schema changes

Two edits, every time, per `PROJECT.md` §9: a numbered file in `migrations/`
applied directly to the live database, *and* the same change added to
`schema.sql` so a fresh database via `reset.sh` matches. Doing only one lets
either the live database or the file for future databases drift, which
already happened once (see `DECISIONS.md` #77's missing-indexes finding).

```bash
sqlite3 jobs.db < migrations/010_whatever.sql   # apply to the live DB
# then hand-edit schema.sql to match
```

Then check it actually matches, rather than assume:

```bash
python -m scripts.check_schema_drift
```

Read-only against `jobs.db` (opened via a `mode=ro` URI, structurally
cannot write to it), compares tables, columns, indexes, and views against
`schema.sql`. Exits 0 if they agree, 1 and a printed list of what's out of
sync if not. Added after `schema.sql` was found to describe four indexes
that had never actually existed on the live database (`DECISIONS.md` #77,
#83), worth running any time you touch `schema.sql` or a migration, not
just when something feels wrong.
