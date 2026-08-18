# jobhunt

A local-first pipeline that pulls job postings from public ATS APIs, filters them
against my criteria, and scores fit against a structured resume bank — so that
reviewing openings takes minutes a day instead of hours a week.

No scraping. No auto-submit. Every source is a documented public JSON endpoint
that companies' own careers pages already call.

## Why

Cold applications through job portals are the lowest-yield channel in a job
search — referrals convert far better. But they still need to happen, and done
by hand they eat 5–10 hours a week: finding postings, reading JDs, judging fit,
tailoring a resume, tracking what went where.

This automates that channel *because* it's low-yield. The goal isn't to make cold
applications convert better — it's to drive their time cost to near zero so that
time can go to referrals and interview prep instead.

## How it works

```
companies.txt ──> probe ──> probe_results.json ──> companies table
                                                        │
                            cron, every 6h ──> fetch ───┤
                                                        v
                                                    jobs table
                                                        │
                             rules only, no LLM ──> filter      (~85% dropped)
                                                        │
                               local model ──> extract requirements
                                                        │
                                  arithmetic ──> score fit
                                                        │
                                                   REVIEW  (human)
                                                        │
                                cloud model ──> tailor ──> Typst ──> PDF
                                                        │
                                                    APPLY  (manual)
                                                        │
                                              outcomes ──> weekly gap report
```

Five ideas do most of the work:

**Public ATS feeds instead of scraping.** Most companies rent their careers page
from Greenhouse, Lever, Ashby, SmartRecruiters, or Workable, and each serves job
data as public JSON. Hitting those endpoints is what the browser does anyway,
minus the rendering — so there's nothing to reverse-engineer and nothing
adversarial.

**Cheap filters before expensive ones.** Deterministic title/location/comp rules
reject ~85% of postings for free. Only survivors reach a model. This ordering is
most of the compute budget.

**The model extracts; arithmetic judges.** Ask an LLM to score fit 0–100 and
nearly everything comes back 72–85 — precise-looking and meaningless. Instead the
model only *extracts* requirements from the JD and *matches* them to resume
bullets, then the score is a ratio over those matches. Every point traces back to
a specific requirement, so a wrong score is debuggable.

**A bullet bank, not prose.** The resume lives as structured YAML holding ~4×
more bullets than any single resume uses. Tailoring selects bullet IDs from that
fixed set, so the model structurally cannot invent employment history.

**The human stays in the loop where it matters.** Review and submission are
manual. Application forms are per-company and brittle, and mass-submitting
generated applications is a pattern recruiters detect. The win is triage and
drafting; the last mile is cheap by hand and expensive to get wrong.

## Quickstart

Requires Python 3.12+, SQLite, [Typst](https://typst.app) (for step 7), and
either [Ollama](https://ollama.com) or an `ANTHROPIC_API_KEY` (for steps 6–7,
see `PROJECT.md` §7a for the tradeoff).

```bash
git clone <this-repo> && cd jobhunt
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # + requirements-dev.txt for pytest

./reset.sh                          # create jobs.db from schema.sql
mkdir -p personal                   # gitignored: your resume, your company list
cp data/companies.example.txt personal/companies.txt
```

You'll also need `personal/resume.yaml` (see `pipeline/resume_bank.py` for the
shape it expects: `contact`, `preferences`, `summaries`, `skills`,
`experience`, `projects`, `education`. PROJECT.md §8 has the full spec).
Everything under `personal/` is gitignored; nothing there ever gets committed.

Edit `personal/companies.txt`, one company per line, `#` comments allowed.
Only add companies you'd take a call from tomorrow; every extra one is a
permanent tax on every review session.

```bash
python -m scripts.probe             # resolve each company to an ATS + slug
python -m scripts.load_companies    # write resolved companies to the DB
```

Expect ~65% to resolve automatically. Slugs are usually the lowercased name with
spaces stripped, but not always (`Harness → harnessinc`, `Nx → nrwl`). For the
rest, open the company's careers page, click into any posting, and read the ATS
and slug out of the URL — faster than automating it.

Run scripts as modules from the repo root. `python scripts/probe.py` will fail on
imports.

```bash
python -m scripts.fetch_all         # populate jobs (step 3)
python -m scripts.filter_all        # rules-only triage (step 5)
python -m scripts.extract_all       # model → requirements table (step 6)
python -m scripts.score_all         # arithmetic fit_score / fit_tier (step 6)
datasette serve jobs.db             # browse /jobs/review_queue to decide
python -m scripts.tailor <job_id>   # tailored resume PDF (step 7)
python -m scripts.log_application <job_id>   # after you actually send it
```

**Every command, every flag, with examples: [`COMMANDS.md`](COMMANDS.md).**
That's the full reference; this section is just enough to get a first run
going.

`pipeline/extract.py`'s `PROVIDER` constant picks the model for steps 6–7:
`"ollama"` (free, local, needs `llama3.2` pulled) or `"claude"` (costs per
call, more accurate, needs `ANTHROPIC_API_KEY`). See `PROJECT.md` §7a.

## Layout

```
schema.sql          source of truth for a FRESH database; jobs.db is gitignored
migrations/         numbered changes applied to the LIVE database (both edits
                    every time, see PROJECT.md §9 and COMMANDS.md's last section)
reset.sh            drop and recreate the DB from schema.sql, destructive
personal/           resume.yaml, companies.txt, probe_results.json, entirely
                    gitignored, nothing here is ever committed
data/               companies.example.txt, the one shipped template
pipeline/           importable library: db, ats, fetch, filters, extract, score,
                    tailor, render, text (shared strip_html/remote regex),
                    resume_bank (single cached resume.yaml loader)
scripts/            entry points, one per pipeline stage, see COMMANDS.md
tests/              pytest, pure functions only, no network/model/real DB
PROJECT.md          architecture, schema rationale, ATS quirks, build plan
DECISIONS.md        numbered architecture decision records, the detailed "why"
COMMANDS.md         every command, every flag, with examples
```

`pipeline/` is imported, `scripts/` is executed. Scripts stay thin: argument
parsing and a call into the library.

## Status

**Steps 1–7 are built; applying has actually started.** 70 companies, ~7,700
postings (grows via cron), filter reduces to ~330 reviewable across 7 role
families. The corpus is fully extracted and scored: **329 scored, 27 apply /
144 stretch / 158 skip.** Resume tailoring (step 7, one Claude call per job →
a page-fit-checked Typst PDF) is built and in real use, not just verified
once. **3 applications logged** as of this writing.

| Step | State |
|---|---|
| 1. Schema | done |
| 2. Company list + ATS probe | done |
| 3. Fetchers + normalizer | done |
| 4. Closed detection + cron | done |
| 5. Cheap filter + role tagging | done |
| 6. Extraction + arithmetic scoring | done |
| 7. Resume tailoring + render | done, in real use |
| 8. Outcome tracking | not started |
| 9. Weekly aggregate gap report | not started |

Per PROJECT.md §11, the rule that actually matters regardless of what's
built: N applications a week, building only in the time left over. Steps 1–7
being done doesn't change that applying is the point.

See PROJECT.md §10 for what "done" means at each step, and DECISIONS.md for
the numbered record of every real bug found and fixed along the way.

## Known limitations

**Workday isn't supported yet**, which is the biggest gap. Google, Microsoft,
Amazon, Apple, Meta, Netflix, Nvidia, GitHub, Shopify, and Atlassian all run
Workday or homegrown systems, so they're invisible to the pipeline. They're
covered by a 20-minute manual check for now. Workday has no documented public
API, but each careers site is an SPA calling a JSON endpoint that can be called
directly — per-tenant and fiddly, but tractable.

**Pagination is only partly handled.** Greenhouse and Ashby return whole boards
in one response; SmartRecruiters caps at 100 per page and Workable uses an opaque
continuation token.

**Source timestamps are unreliable** across the board — Lever uses epoch
milliseconds, Greenhouse's `updated_at` changes on any edit, Workday returns
English text like "Posted 3 Days Ago". The pipeline stamps its own
`first_seen_at` on insert and ignores what the source claims.

**Fit scores skew pessimistic** for senior roles, which routinely ask for 5–8
years. Those requirements are wishlists; the gap list is more useful than the
number.

## What this deliberately doesn't do

- Scrape LinkedIn or Indeed (ToS, anti-bot, account risk)
- Auto-submit applications
- Run on anything more than SQLite and cron — one user, ~200 new postings a day
- Ship a web UI; Datasette over the SQLite file is the review surface

## License

MIT
