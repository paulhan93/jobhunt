import argparse

from pipeline.db import db_session
from pipeline.filters import classify


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true",
                    help="return filtered/rejected jobs to 'new' first")
    args = ap.parse_args()

    with db_session() as conn:
        if args.reset:
            n = conn.execute(
                """UPDATE jobs SET status = 'new', role_family = NULL,
                                   reject_reason = NULL
                   WHERE status IN ('filtered', 'rejected')"""
            ).rowcount
            print(f"reset {n} jobs\n")

        rows = conn.execute(
            """SELECT id, title, location, remote, comp_min, comp_max
               FROM jobs WHERE status = 'new' AND closed_at IS NULL"""
        ).fetchall()

        updates = []
        for r in rows:
            status, family, reason = classify(r)
            updates.append((status, family, reason, r["id"]))

        conn.executemany(
            "UPDATE jobs SET status=?, role_family=?, reject_reason=? WHERE id=?",
            updates,
        )

        print(f"classified {len(rows)} jobs\n")

        for row in conn.execute(
            """SELECT COALESCE(reject_reason, 'PASSED') AS reason, COUNT(*) n
               FROM jobs WHERE status IN ('filtered', 'rejected')
               GROUP BY reason ORDER BY n DESC"""
        ):
            print(f"  {row['reason']:22} {row['n']}")

        print()
        for row in conn.execute(
            """SELECT role_family, COUNT(*) n FROM jobs
               WHERE status = 'filtered' GROUP BY role_family ORDER BY n DESC"""
        ):
            print(f"  {row['role_family']:12} {row['n']}")


if __name__ == "__main__":
    main()
