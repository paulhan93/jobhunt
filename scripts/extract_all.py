import argparse
import json

from pipeline.db import get_conn
from pipeline.extract import (
    BATCH_THRESHOLD,
    PROVIDER,
    extract_comp,
    extract_requirements,
    extract_requirements_batch,
    load_bullet_bank,
    match_evidence,
    match_evidence_batch,
    strip_html,
)
from pipeline.filters import COMP_FLOOR

MAX_ATTEMPTS = 3


def _fails_comp_floor(job, comp_min, comp_max) -> bool:
    """True if newly-discovered comp (not known at filter time) is below the
    floor filters.py couldn't check yet. Greenhouse never exposes comp
    structurally at all, and Ashby only ~47% of the time (PROJECT.md §5) —
    for those jobs, comp_min/comp_max are NULL when filter_all.py runs, so
    the comp-floor check there correctly has nothing to check against and
    passes the job through. The real number is often only discovered here,
    via regex over the JD text, one stage after the filter gate already ran
    — decision 66. Only re-check comp that's actually new (job["comp_min"]
    was None): if the job already had comp at filter time, it was already
    correctly evaluated then, under either the old or fixed (decision 59)
    logic."""
    if job["comp_min"] is not None or comp_min is None:
        return False
    effective = comp_max if comp_max is not None else comp_min
    return effective < COMP_FLOOR


def process_job(conn, job, bullets) -> str:
    """Returns "extracted" or "comp_rejected" — the caller needs to tell
    these apart (comp_rejected shouldn't be logged/counted as a successful
    extraction, and it never spent a model call), same reasoning as
    _fails_comp_floor's docstring."""
    text = strip_html(job["description"] or "")
    comp_min, comp_max, comp_currency = extract_comp(text)

    if _fails_comp_floor(job, comp_min, comp_max):
        conn.execute(
            "UPDATE jobs SET status='rejected', reject_reason='comp_below_floor', "
            "comp_min=?, comp_max=?, comp_currency=? WHERE id=?",
            (comp_min, comp_max, comp_currency, job["id"]),
        )
        return "comp_rejected"

    # No section-trim here (previously extract_relevant_section()) — the
    # regex marker matching had a confirmed failure mode: a marker matching
    # mid-sentence prose (e.g. "requirements" inside "...customer
    # requirements like...") could silently discard the real qualifications
    # section. The extraction prompt already instructs the model to ignore
    # perks/benefits/team fluff, and extract_comp() above already runs on the
    # full text for the same reason. See DECISIONS.md for the verification.
    requirements = extract_requirements(text)
    matches = match_evidence(requirements, bullets)

    rows = []
    for i, r in enumerate(requirements):
        bullet_ids = matches.get(i, [])
        rows.append((
            job["id"],
            r["text"],
            r["kind"],
            r.get("skill_key"),
            r.get("years_required"),
            json.dumps(bullet_ids) if bullet_ids else None,
        ))

    conn.executemany(
        """INSERT INTO requirements
               (job_id, text, kind, skill_key, years_required, matched_bullets)
           VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )

    update = ["status = 'extracted'"]
    params: list = []
    if job["comp_min"] is None and comp_min is not None:
        update += ["comp_min = ?", "comp_max = ?", "comp_currency = ?"]
        params += [comp_min, comp_max, comp_currency]
    params.append(job["id"])
    conn.execute(f"UPDATE jobs SET {', '.join(update)} WHERE id = ?", params)
    return "extracted"


def process_batch(conn, jobs, bullets) -> None:
    """Claude-only path for large runs (PROJECT.md discussion: Batch API is
    50% cheaper and one submission instead of N round trips, but only makes
    sense above some job count — see BATCH_THRESHOLD in pipeline/extract.py).
    Preprocessing is per-job and cheap (no model call); the two model calls
    happen once each for the whole set. Results are still written back and
    committed per job, same resilience reasoning as the per-job loop below —
    a crash while writing ~250 parsed results shouldn't lose all of them."""
    texts, comps = {}, {}
    comp_rejected = []
    for job in jobs:
        text = strip_html(job["description"] or "")
        texts[job["id"]] = text
        comp_min, comp_max, comp_currency = extract_comp(text)
        comps[job["id"]] = (comp_min, comp_max, comp_currency)

        if _fails_comp_floor(job, comp_min, comp_max):
            conn.execute(
                "UPDATE jobs SET status='rejected', reject_reason='comp_below_floor', "
                "comp_min=?, comp_max=?, comp_currency=? WHERE id=?",
                (comp_min, comp_max, comp_currency, job["id"]),
            )
            comp_rejected.append(job["id"])
    conn.commit()

    # Same check as process_job(), just applied before the batch is built —
    # a job whose newly-discovered comp fails the floor shouldn't spend a
    # model call on extraction it's about to be rejected for anyway.
    jobs = [j for j in jobs if j["id"] not in comp_rejected]
    if comp_rejected:
        print(f"  {len(comp_rejected)} job(s) rejected on comp floor before "
              f"extraction (saved {len(comp_rejected)} model call(s)): "
              f"{comp_rejected}")

    print(f"submitting extraction batch for {len(jobs)} jobs...")
    req_map = extract_requirements_batch(
        {str(job["id"]): texts[job["id"]] for job in jobs}
    )

    print("submitting matching batch...")
    match_input = {str(job["id"]): req_map.get(str(job["id"]), []) for job in jobs}
    match_map = match_evidence_batch(match_input, bullets)

    n_ok, n_err, n_reqs = 0, 0, 0
    for job in jobs:
        jid = str(job["id"])
        attempts = job["attempts"] + 1
        conn.execute("UPDATE jobs SET attempts = ? WHERE id = ?", (attempts, job["id"]))

        requirements = req_map.get(jid)
        if requirements is None:  # this job's batch request errored/expired
            n_err += 1
            status = "error" if attempts >= MAX_ATTEMPTS else "filtered"
            conn.execute(
                "UPDATE jobs SET status = ?, last_error = ? WHERE id = ?",
                (status, "batch extraction request failed", job["id"]),
            )
            conn.commit()
            print(f"  FAIL [{job['id']:>6}] {job['title'][:70]}")
            continue

        matches = match_map.get(jid, {})
        if matches is None:  # this job's batch matching request errored/expired
            n_err += 1
            status = "error" if attempts >= MAX_ATTEMPTS else "filtered"
            conn.execute(
                "UPDATE jobs SET status = ?, last_error = ? WHERE id = ?",
                (status, "batch matching request failed", job["id"]),
            )
            conn.commit()
            print(f"  FAIL [{job['id']:>6}] {job['title'][:70]}")
            continue

        try:
            rows = [
                (
                    job["id"], r["text"], r["kind"], r.get("skill_key"),
                    r.get("years_required"),
                    json.dumps(matches.get(i, [])) if matches.get(i) else None,
                )
                for i, r in enumerate(requirements)
            ]
            conn.executemany(
                """INSERT INTO requirements
                       (job_id, text, kind, skill_key, years_required, matched_bullets)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                rows,
            )

            comp_min, comp_max, comp_currency = comps[job["id"]]
            update = ["status = 'extracted'"]
            params: list = []
            if job["comp_min"] is None and comp_min is not None:
                update += ["comp_min = ?", "comp_max = ?", "comp_currency = ?"]
                params += [comp_min, comp_max, comp_currency]
            params.append(job["id"])
            conn.execute(f"UPDATE jobs SET {', '.join(update)} WHERE id = ?", params)
            conn.commit()

            n_reqs += len(rows)
            n_ok += 1
            print(f"  ok   [{job['id']:>6}] {job['title'][:70]}")
        except Exception as e:
            # Same resilience rule as the non-batch loop below: one malformed
            # result must not abort the rest of the batch (up to hundreds of
            # jobs) — mark this job and move on.
            conn.rollback()
            n_err += 1
            status = "error" if attempts >= MAX_ATTEMPTS else "filtered"
            conn.execute(
                "UPDATE jobs SET status = ?, last_error = ? WHERE id = ?",
                (status, str(e), job["id"]),
            )
            conn.commit()
            print(f"  FAIL [{job['id']:>6}] {job['title'][:70]}: {e}")

    print(f"\n{n_ok} extracted, {n_err} failed, {len(comp_rejected)} rejected "
          f"on comp floor, {n_reqs} requirements inserted (batch mode)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                     help="process at most N jobs (for dry runs)")
    ap.add_argument("--job-id", type=int, default=None,
                     help="process only this job id (must already be "
                          "'filtered') — for a manual one-off case (e.g. "
                          "applying below the comp floor on purpose) without "
                          "pulling in every other job sitting at 'filtered'")
    ap.add_argument("--reset", action="store_true",
                     help="delete existing requirements and return "
                          "extracted/scored/reviewed/applied jobs to "
                          "'filtered' first (e.g. after a prompt/preprocessing "
                          "fix that needs re-extraction, not just re-scoring)")
    args = ap.parse_args()

    bullets = load_bullet_bank()
    print(f"loaded {len(bullets)} resume bullets\n")

    # A plain connection, committed per-job below (not one big transaction for
    # the whole run) — these are slow model calls over up to hundreds of jobs,
    # and a crash near the end shouldn't roll back everything already done.
    conn = get_conn()
    try:
        if args.reset:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM jobs "
                "WHERE status IN ('extracted','scored','reviewed','applied')"
            ).fetchall()]
            if ids:
                conn.executemany(
                    "DELETE FROM requirements WHERE job_id=?", [(i,) for i in ids]
                )
                conn.execute(
                    f"UPDATE jobs SET status='filtered', fit_score=NULL, "
                    f"fit_tier=NULL, attempts=0, last_error=NULL "
                    f"WHERE id IN ({','.join('?' * len(ids))})",
                    ids,
                )
                conn.commit()
            print(f"reset {len(ids)} jobs (deleted their requirements)\n")

        if args.job_id:
            query = """SELECT * FROM jobs
                       WHERE id = ? AND status = 'filtered' AND attempts < ?
                         AND closed_at IS NULL"""
            params = [args.job_id, MAX_ATTEMPTS]
        else:
            query = """SELECT * FROM jobs
                       WHERE status = 'filtered' AND attempts < ?
                         AND closed_at IS NULL
                       ORDER BY id"""
            params = [MAX_ATTEMPTS]
            if args.limit:
                query += " LIMIT ?"
                params.append(args.limit)
        rows = conn.execute(query, params).fetchall()

        if args.job_id and not rows:
            raise SystemExit(
                f"job {args.job_id} isn't eligible (not 'filtered', "
                f"already at max attempts, or closed)"
            )

        if PROVIDER == "claude" and len(rows) >= BATCH_THRESHOLD:
            process_batch(conn, rows, bullets)
            return

        n_ok, n_err, n_reqs, n_comp_rejected = 0, 0, 0, 0
        for job in rows:
            conn.execute(
                "UPDATE jobs SET attempts = attempts + 1 WHERE id = ?", (job["id"],)
            )
            try:
                before = conn.execute(
                    "SELECT COUNT(*) FROM requirements WHERE job_id = ?",
                    (job["id"],),
                ).fetchone()[0]
                outcome = process_job(conn, job, bullets)
                after = conn.execute(
                    "SELECT COUNT(*) FROM requirements WHERE job_id = ?",
                    (job["id"],),
                ).fetchone()[0]
                n_reqs += after - before
                if outcome == "comp_rejected":
                    n_comp_rejected += 1
                    print(f"  $$   [{job['id']:>6}] {job['title'][:70]} "
                          f"(comp below floor, no model call spent)")
                else:
                    n_ok += 1
                    print(f"  ok   [{job['id']:>6}] {job['title'][:70]}")
            except Exception as e:
                conn.rollback()  # discard any partial INSERTs from this job
                n_err += 1
                attempts = job["attempts"] + 1
                status = "error" if attempts >= MAX_ATTEMPTS else "filtered"
                conn.execute(
                    "UPDATE jobs SET attempts = ?, status = ?, last_error = ? "
                    "WHERE id = ?",
                    (attempts, status, str(e), job["id"]),
                )
                print(f"  FAIL [{job['id']:>6}] {job['title'][:70]}: {e}")

            conn.commit()

        print(f"\n{n_ok} extracted, {n_err} failed, {n_comp_rejected} rejected "
              f"on comp floor, {n_reqs} requirements inserted")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
