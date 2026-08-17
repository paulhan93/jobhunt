"""Backfill match_strength (migration 007) onto requirements that were
matched before strength ratings existed. Re-runs ONLY match_evidence, not
extract_requirements - the requirement text/kind/skill_key/years_required
already extracted don't change, only how well each one's evidence is rated.
Cheaper and lower-risk than extract_all.py --reset, which would also burn a
call re-extracting requirements that don't need to change.

Idempotent/resumable per PROJECT.md §9 convention: only touches requirements
rows where match_strength IS NULL, committed per job.
"""
import argparse
import json

from pipeline.db import get_conn
from pipeline.extract import BATCH_THRESHOLD, PROVIDER, load_bullet_bank, match_evidence, match_evidence_batch


def _rows_to_requirements(rows) -> list[dict]:
    return [
        {"text": r["text"], "kind": r["kind"], "skill_key": r["skill_key"],
         "years_required": r["years_required"]}
        for r in rows
    ]


def _write_match(conn, req_id: int, m: dict) -> None:
    conn.execute(
        "UPDATE requirements SET matched_bullets=?, match_strength=? WHERE id=?",
        (json.dumps(m["bullet_ids"]) if m["bullet_ids"] else None, m["strength"], req_id),
    )


def process_job(conn, job_id: int, bullets: dict[str, str]) -> int:
    rows = conn.execute(
        "SELECT id, text, kind, skill_key, years_required FROM requirements "
        "WHERE job_id=? AND match_strength IS NULL ORDER BY id",
        (job_id,),
    ).fetchall()
    if not rows:
        return 0

    requirements = _rows_to_requirements(rows)
    matches = match_evidence(requirements, bullets)
    for i, row in enumerate(rows):
        m = matches.get(i, {"bullet_ids": [], "strength": "none"})
        _write_match(conn, row["id"], m)
    return len(rows)


def process_batch(conn, job_ids: list[int], bullets: dict[str, str]) -> None:
    per_job_rows = {
        jid: conn.execute(
            "SELECT id, text, kind, skill_key, years_required FROM requirements "
            "WHERE job_id=? AND match_strength IS NULL ORDER BY id",
            (jid,),
        ).fetchall()
        for jid in job_ids
    }
    per_job_rows = {jid: rows for jid, rows in per_job_rows.items() if rows}
    if not per_job_rows:
        print("nothing to rematch")
        return

    print(f"submitting rematch batch for {len(per_job_rows)} jobs...")
    match_input = {
        str(jid): _rows_to_requirements(rows) for jid, rows in per_job_rows.items()
    }
    match_map = match_evidence_batch(match_input, bullets)

    n_ok, n_err, n_reqs = 0, 0, 0
    for jid, rows in per_job_rows.items():
        matches = match_map.get(str(jid))
        if matches is None:
            n_err += 1
            print(f"  FAIL [{jid:>6}] batch request failed")
            continue
        for i, row in enumerate(rows):
            m = matches.get(i, {"bullet_ids": [], "strength": "none"})
            _write_match(conn, row["id"], m)
        conn.commit()
        n_ok += 1
        n_reqs += len(rows)
        print(f"  ok   [{jid:>6}] {len(rows)} requirement(s)")

    print(f"\n{n_ok} jobs rematched, {n_err} failed, {n_reqs} requirements updated")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                     help="process at most N jobs (for a small sample check "
                          "before committing to a full run)")
    ap.add_argument("--job-id", type=int, default=None,
                     help="rematch only this job id")
    args = ap.parse_args()

    bullets = load_bullet_bank()
    print(f"loaded {len(bullets)} resume bullets\n")

    conn = get_conn()
    try:
        if args.job_id:
            job_ids = [args.job_id]
        else:
            query = """SELECT DISTINCT job_id FROM requirements
                       WHERE match_strength IS NULL ORDER BY job_id"""
            params: list = []
            if args.limit:
                query += " LIMIT ?"
                params.append(args.limit)
            job_ids = [r["job_id"] for r in conn.execute(query, params).fetchall()]

        print(f"{len(job_ids)} job(s) need rematching\n")
        if not job_ids:
            return

        if PROVIDER == "claude" and len(job_ids) >= BATCH_THRESHOLD:
            process_batch(conn, job_ids, bullets)
            return

        n_reqs = 0
        for jid in job_ids:
            n = process_job(conn, jid, bullets)
            conn.commit()
            n_reqs += n
            print(f"  ok   [{jid:>6}] {n} requirement(s)")
        print(f"\n{len(job_ids)} jobs rematched, {n_reqs} requirements updated")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
