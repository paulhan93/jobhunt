import argparse

from pipeline.db import db_session


def main():
    ap = argparse.ArgumentParser(
        description="Record a manually-submitted application and move the job to 'applied'."
    )
    ap.add_argument("job_id", type=int)
    ap.add_argument("--resume-version", default=None)
    ap.add_argument("--referral", default=None)
    ap.add_argument("--notes", default=None)
    args = ap.parse_args()

    with db_session() as conn:
        job = conn.execute(
            "SELECT id, title, status, fit_score, fit_tier FROM jobs WHERE id = ?",
            (args.job_id,),
        ).fetchone()
        if job is None:
            raise SystemExit(f"no job with id {args.job_id}")
        if job["status"] == "applied":
            raise SystemExit(f"job {args.job_id} is already logged as applied")

        conn.execute(
            "INSERT INTO applications "
            "(job_id, resume_version, referral, notes, "
            " fit_score_at_application, fit_tier_at_application) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (args.job_id, args.resume_version, args.referral, args.notes,
             job["fit_score"], job["fit_tier"]),
        )
        conn.execute("UPDATE jobs SET status='applied' WHERE id=?", (args.job_id,))

    print(f"logged application for [{job['id']}] {job['title']}")


if __name__ == "__main__":
    main()
