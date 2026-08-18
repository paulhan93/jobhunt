import json
import time

import httpx

from pipeline.db import get_conn
from pipeline.fetch import fetch_board

SLEEP = 0.4

UPSERT = """
INSERT INTO jobs (
    company_id, source, source_job_id, title, location, remote,
    description, apply_url, comp_min, comp_max, comp_currency, raw_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (source, source_job_id) DO UPDATE SET
    last_seen_at  = datetime('now'),
    closed_at     = NULL,
    title         = excluded.title,
    location      = excluded.location,
    remote        = excluded.remote,
    description   = COALESCE(excluded.description, jobs.description),
    apply_url     = excluded.apply_url,
    comp_min      = excluded.comp_min,
    comp_max      = excluded.comp_max,
    comp_currency = excluded.comp_currency,
    raw_json      = excluded.raw_json
"""


def _write_company(conn, c, jobs) -> tuple[int, int]:
    """Upsert one company's jobs and run closed-detection for it. Returns
    (inserted, closed). Committed by the caller as one unit per company,
    NOT wrapped in get_conn()'s own context manager, so a DB-write failure on
    company 50 of 70 can't roll back the 49 companies already committed
    before it (the exact bug class PROJECT.md §9 warns about and
    DECISIONS.md #45 already fixed once in extract_all.py, previously still
    live here)."""
    # Scoped to this company (idx_jobs_company) rather than a full-table
    # COUNT(*), same delta, since this company's upsert is the only write
    # happening between the two counts, but doesn't re-scan the whole (and
    # growing) jobs table on every one of the 70 companies just to log an
    # insert count.
    before = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE company_id = ?", (c["id"],)
    ).fetchone()[0]

    conn.executemany(UPSERT, [
        (c["id"], j.source, j.source_job_id, j.title, j.location,
         j.remote, j.description, j.apply_url, j.comp_min,
         j.comp_max, j.comp_currency, json.dumps(j.raw))
        for j in jobs
    ])

    after = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE company_id = ?", (c["id"],)
    ).fetchone()[0]
    inserted = after - before

    # Closed detection: anything still open for this company that wasn't in
    # the response has been filled or pulled. Guarded against truncated
    # responses mass-closing live jobs.
    seen_ids = [j.source_job_id for j in jobs]
    open_count = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE company_id = ? AND closed_at IS NULL",
        (c["id"],),
    ).fetchone()[0]

    closed = 0
    if open_count and len(seen_ids) < open_count * 0.7:
        print(f"  WARN  {c['name']}: {len(seen_ids)} fetched vs "
              f"{open_count} open, skipping closed detection")
    else:
        placeholders = ",".join("?" * len(seen_ids)) or "NULL"
        closed = conn.execute(
            f"""UPDATE jobs SET closed_at = datetime('now')
                WHERE company_id = ? AND closed_at IS NULL
                  AND source_job_id NOT IN ({placeholders})""",
            [c["id"], *seen_ids],
        ).rowcount

    return inserted, closed


def main():
    # Plain connection, committed per company below (not one big transaction
    # for the whole 70-company run), same resilience reasoning as
    # extract_all.py: a crash or bad response near company 50 shouldn't roll
    # back everything already fetched and written.
    conn = get_conn()
    try:
        companies = conn.execute(
            "SELECT id, name, ats, slug FROM companies WHERE active = 1 ORDER BY name"
        ).fetchall()

        totals = {"new": 0, "seen": 0, "closed": 0, "errors": 0}

        with httpx.Client(follow_redirects=True) as client:
            for c in companies:
                try:
                    jobs = fetch_board(client, c["ats"], c["slug"])
                except Exception as e:
                    print(f"  ERROR {c['name']}: {type(e).__name__}: {e}")
                    totals["errors"] += 1
                    time.sleep(SLEEP)
                    continue

                try:
                    inserted, closed = _write_company(conn, c, jobs)
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    print(f"  ERROR {c['name']}: db write failed: "
                          f"{type(e).__name__}: {e}")
                    totals["errors"] += 1
                    time.sleep(SLEEP)
                    continue

                totals["new"] += inserted
                totals["seen"] += len(jobs)
                totals["closed"] += closed

                note = f"  {c['name']:24} {len(jobs):4} jobs  +{inserted} new"
                if closed:
                    note += f"  -{closed} closed"
                print(note)

                time.sleep(SLEEP)

        print(f"\n{totals['seen']} fetched, {totals['new']} new, "
              f"{totals['closed']} closed, {totals['errors']} errors")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
