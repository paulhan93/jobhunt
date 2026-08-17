import argparse

from pipeline.db import get_conn
from pipeline.score import load_skill_years, load_total_years, score_job


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                     help="process at most N jobs (for dry runs)")
    ap.add_argument("--reset", action="store_true",
                     help="return scored/reviewed/applied jobs to 'extracted' "
                          "so score_job() re-runs against their existing "
                          "requirements — for when only pipeline/score.py's "
                          "formula or thresholds changed, not extraction. No "
                          "API calls, requirements are left untouched (unlike "
                          "extract_all.py --reset, which deletes them)")
    args = ap.parse_args()

    skill_years = load_skill_years()
    total_years = load_total_years()

    # Plain connection with a longer busy_timeout, committed per-job — not one
    # giant transaction, so this doesn't collide with extract_all.py's own
    # periodic writes when both are running at once.
    conn = get_conn()
    try:
        if args.reset:
            # Includes 'applied' deliberately, not just 'scored': jobs.fit_score
            # is documented as live/mutable (schema.sql rationale, decision 52)
            # precisely so a formula change can re-score even applied jobs —
            # applications.fit_score_at_application already snapshots what the
            # score was at apply time, so re-scoring here can't lose that
            # history. Different risk profile than filter_all.py --reset, which
            # deliberately skips reviewed/applied because re-filtering can
            # change role_family/reject_reason, not just a number.
            n = conn.execute(
                """UPDATE jobs SET status = 'extracted', fit_score = NULL, fit_tier = NULL
                   WHERE status IN ('scored', 'reviewed', 'applied')"""
            ).rowcount
            conn.commit()
            print(f"reset {n} jobs to 'extracted' (requirements untouched)\n")

        query = "SELECT id, title FROM jobs WHERE status = 'extracted' ORDER BY id"
        params: list = []
        if args.limit:
            query += " LIMIT ?"
            params.append(args.limit)
        jobs = conn.execute(query, params).fetchall()

        tiers = {"apply": 0, "stretch": 0, "skip": 0, "error": 0}
        for job in jobs:
            reqs = conn.execute(
                "SELECT kind, skill_key, years_required, matched_bullets, match_strength "
                "FROM requirements WHERE job_id = ?",
                (job["id"],),
            ).fetchall()

            fit, tier = score_job(reqs, skill_years, total_years)

            if tier is None:
                tiers["error"] += 1
                conn.execute(
                    "UPDATE jobs SET status='error', attempts=attempts+1, "
                    "last_error='no requirements extracted' WHERE id=?",
                    (job["id"],),
                )
                conn.commit()
                print(f"  {'--':>5}  {'error':<8} [{job['id']:>6}] {job['title'][:60]}")
                continue

            tiers[tier] += 1
            conn.execute(
                "UPDATE jobs SET status='scored', fit_score=?, fit_tier=? WHERE id=?",
                (fit, tier, job["id"]),
            )
            conn.commit()
            print(f"  {fit:>5.1f}  {tier:<8} [{job['id']:>6}] {job['title'][:60]}")

        print(f"\nscored {len(jobs)} jobs")
        for tier, n in tiers.items():
            print(f"  {tier:8} {n}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
