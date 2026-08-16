import argparse
import json

from pipeline.db import get_conn
from pipeline.extract import (
    BATCH_THRESHOLD,
    PROVIDER,
    extract_comp,
    extract_relevant_section,
    extract_requirements,
    extract_requirements_batch,
    load_bullet_bank,
    match_evidence,
    match_evidence_batch,
    strip_html,
)

MAX_ATTEMPTS = 3


def process_job(conn, job, bullets) -> None:
    text = strip_html(job["description"] or "")
    comp_min, comp_max, comp_currency = extract_comp(text)
    section = extract_relevant_section(text)

    requirements = extract_requirements(section)
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


def process_batch(conn, jobs, bullets) -> None:
    """Claude-only path for large runs (PROJECT.md discussion: Batch API is
    50% cheaper and one submission instead of N round trips, but only makes
    sense above some job count — see BATCH_THRESHOLD in pipeline/extract.py).
    Preprocessing is per-job and cheap (no model call); the two model calls
    happen once each for the whole set. Results are still written back and
    committed per job, same resilience reasoning as the per-job loop below —
    a crash while writing ~250 parsed results shouldn't lose all of them."""
    texts, comps, sections = {}, {}, {}
    for job in jobs:
        text = strip_html(job["description"] or "")
        texts[job["id"]] = text
        comps[job["id"]] = extract_comp(text)
        sections[job["id"]] = extract_relevant_section(text)

    print(f"submitting extraction batch for {len(jobs)} jobs...")
    req_map = extract_requirements_batch(
        {str(job["id"]): sections[job["id"]] for job in jobs}
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

    print(f"\n{n_ok} extracted, {n_err} failed, {n_reqs} requirements inserted (batch mode)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                     help="process at most N jobs (for dry runs)")
    args = ap.parse_args()

    bullets = load_bullet_bank()
    print(f"loaded {len(bullets)} resume bullets\n")

    # A plain connection, committed per-job below (not one big transaction for
    # the whole run) — these are slow model calls over up to hundreds of jobs,
    # and a crash near the end shouldn't roll back everything already done.
    conn = get_conn()
    try:
        query = """SELECT * FROM jobs
                   WHERE status = 'filtered' AND attempts < ?
                     AND closed_at IS NULL
                   ORDER BY id"""
        params = [MAX_ATTEMPTS]
        if args.limit:
            query += " LIMIT ?"
            params.append(args.limit)
        rows = conn.execute(query, params).fetchall()

        if PROVIDER == "claude" and len(rows) >= BATCH_THRESHOLD:
            process_batch(conn, rows, bullets)
            return

        n_ok, n_err, n_reqs = 0, 0, 0
        for job in rows:
            conn.execute(
                "UPDATE jobs SET attempts = attempts + 1 WHERE id = ?", (job["id"],)
            )
            try:
                before = conn.execute(
                    "SELECT COUNT(*) FROM requirements WHERE job_id = ?",
                    (job["id"],),
                ).fetchone()[0]
                process_job(conn, job, bullets)
                after = conn.execute(
                    "SELECT COUNT(*) FROM requirements WHERE job_id = ?",
                    (job["id"],),
                ).fetchone()[0]
                n_reqs += after - before
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

        print(f"\n{n_ok} extracted, {n_err} failed, {n_reqs} requirements inserted")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
