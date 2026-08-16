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

Requires Python 3.12+, SQLite, and (for step 6 onward) [Ollama](https://ollama.com).

```bash
git clone <this-repo> && cd jobhunt
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

./reset.sh                          # create jobs.db from schema.sql
cp data/companies.example.txt data/companies.txt
```

Edit `data/companies.txt` — one company per line, `#` comments allowed. Only add
companies you'd take a call from tomorrow; every extra one is a permanent tax on
every review session.

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
python -m scripts.extract_all       # local model → requirements table (step 6)
python -m scripts.score_all         # arithmetic fit_score / fit_tier (step 6)
datasette serve jobs.db             # browse, or ?status__exact=scored to review
```

`extract_all`/`score_all` need [Ollama](https://ollama.com) running locally
with `llama3.2` pulled — that's the default (`PROVIDER = "ollama"` in
`pipeline/extract.py`). Both scripts accept `--limit N` for a dry run before
committing to a full pass.

## Layout

```
schema.sql          source of truth for the database; jobs.db is disposable output
reset.sh            drop and recreate the DB
resume.yaml         bullet bank (tagged, ~8 bullets per role)
data/               companies.txt, probe_results.json
pipeline/           importable library — db, ats, fetch, filters, extract, score
scripts/            entry points — probe, load_companies, fetch_all, filter_all,
                    extract_all, score_all, report (report not yet built)
PROJECT.md          architecture, schema rationale, ATS quirks, build plan
```

`pipeline/` is imported, `scripts/` is executed. Scripts stay thin: argument
parsing and a call into the library.

## Status

**Steps 1–6 complete — the MVP is done.** 70 companies resolved, ~7,660
postings, filter reduces to 252 reviewable. Extraction and scoring are built
and running against the backlog on a local model by default, with an optional
Claude API path for a one-time quality re-run (see PROJECT.md §7a).

| Step | State |
|---|---|
| 1. Schema | done |
| 2. Company list + ATS probe | done |
| 3. Fetchers + normalizer | done |
| 4. Closed detection + cron | done |
| 5. Cheap filter + role tagging | done |
| 6. Extraction + arithmetic scoring | done — MVP ends here |
| 7. Resume tailoring + render | next, but see below |
| 8. Outcome tracking | |
| 9. Weekly aggregate gap report | |

Per PROJECT.md §11: the MVP being done means the rule kicks in — stop building,
apply to what's already scored for a week before touching step 7.

See PROJECT.md §10 for what "done" means at each step.

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
