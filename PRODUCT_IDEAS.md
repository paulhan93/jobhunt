# Product Ideas

Market-facing and mission-level notes — separate from `JOBHUNT_PROJECT.md`,
which stays pure engineering source-of-truth (architecture, schema,
conventions). This file is for ideas about where the project could go if it
ever goes beyond personal use, informed by what competitors are already doing.

Not urgent. Per `JOBHUNT_PROJECT.md` §11: this is backlog for later, not a
reason to start building again. Steps 1–6 are done; the current priority is
applying, not adding features.

---

## Competitor scan (2026-08-16)

Auto-apply bots: [FastApply](https://fastapply.co/), JobCopilot, AIApply,
[Loopcv](https://www.loopcv.pro/).
Matching platforms: [Simplify](https://simplify.jobs/), [Jobright](https://jobright.ai/),
[RippleMatch](https://ripplematch.com/), Mployee.me / Job Match Pro, Autojob.

## Ideas worth revisiting

- **Cover-letter generation as a step-7 extension.** FastApply and AIApply both
  generate cover letters alongside resumes. Fits the existing bullet-ID-selection
  pattern from `JOBHUNT_PROJECT.md` §7/step 7 — model picks 2–3 bullets + writes
  connective tissue, still validated against the known bullet set, so it doesn't
  break the "never let a model invent employment history" rule.
- **Company info/news surfaced at review time.** Already an idea from earlier
  notes; RippleMatch and Simplify both do this too, which is a signal it's
  worth keeping, not a new idea on its own.
- **`role_family` priority as a scoring input.** RippleMatch/Jobright weight
  career trajectory, not just current skills. This pipeline already encodes
  trajectory manually via the role-family priority order (§2) but doesn't feed
  it into `fit_score` (§7c) — only into manual review order. A small weight
  term keyed to `role_family` could close that gap without needing a model.
- **Referral/contacts nudge — re-confirmed, not new.** Simplify's "recruiter
  intros" pitch validates the existing engineering-backlog item (`contacts`
  prompt at review time, in `JOBHUNT_PROJECT.md` §10). Per §11 this is
  higher-yield than any pipeline feature — bump ahead of new pipeline stages
  when picking backlog work.

## Explicitly rejected — evaluated and discarded on purpose

Don't re-propose these without a reason something changed:

- **Auto-apply / auto-submit** (Loopcv, JobCopilot, FastApply, AIApply) —
  directly contradicts `JOBHUNT_PROJECT.md` §1's non-goals. Mass-submission
  risk (getting flagged as a bulk applicant) is the whole reason submission is
  manual here. It's also the main differentiator from every competitor above,
  not a gap to close.
- **LLM-generated 0–100 "match score."** Contradicts §7's core thesis —
  arithmetic over extracted data, not an LLM vibes number that "feels precise
  and means almost nothing."
- **Browser-extension scraping of LinkedIn/Indeed.** Contradicts §1's
  ToS/anti-bot non-goal.

## What's actually differentiated (for the pitch, if this ever goes public)

1. Manual submission by design — every competitor competes on auto-apply
   volume; this project bets that's the wrong game.
2. Arithmetic scoring with a traceable requirement → bullet edge, instead of
   an opaque match percentage.
3. Local-first — resume data stays on-device instead of living on a SaaS
   vendor's servers.
