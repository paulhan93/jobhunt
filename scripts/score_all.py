import argparse

from pipeline.db import get_conn
from pipeline.score import load_skill_years, score_job


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                     help="process at most N jobs (for dry runs)")
    args = ap.parse_args()

    skill_years = load_skill_years()

    # Plain connection with a longer busy_timeout, committed per-job — not one
    # giant transaction, so this doesn't collide with extract_all.py's own
    # periodic writes when both are running at once.
    conn = get_conn()
    try:
        query = "SELECT id, title FROM jobs WHERE status = 'extracted' ORDER BY id"
        params: list = []
        if args.limit:
            query += " LIMIT ?"
            params.append(args.limit)
        jobs = conn.execute(query, params).fetchall()

        tiers = {"apply": 0, "stretch": 0, "skip": 0}
        for job in jobs:
            reqs = conn.execute(
                "SELECT kind, skill_key, years_required, matched_bullets "
                "FROM requirements WHERE job_id = ?",
                (job["id"],),
            ).fetchall()

            fit, tier = score_job(reqs, skill_years)
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
