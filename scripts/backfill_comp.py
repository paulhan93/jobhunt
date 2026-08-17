"""Backfill comp_min/comp_max/comp_currency for jobs that already have a
description but no comp on file - regex-only (extract_comp), no model calls,
no cost. Real gap found 2026-08-17: some already-extracted jobs were
processed before a comp-regex fix and never got the retroactive benefit,
since extract_all.py only fills comp on the run that first extracts a job.

Deliberately excludes 'rejected' jobs - discovering comp there could flip a
comp_below_floor rejection, a more sensitive change than a plain backfill
(see PROJECT.md's comp-floor-timing discussion, decision 66). This script
only ever fills a NULL, never overwrites an existing value.
"""
import argparse

from pipeline.db import get_conn
from pipeline.extract import extract_comp, strip_html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    conn = get_conn()
    try:
        query = """SELECT id, title, description FROM jobs
                   WHERE comp_min IS NULL
                     AND status IN ('filtered','extracted','scored','reviewed','applied')"""
        params: list = []
        if args.limit:
            query += " LIMIT ?"
            params.append(args.limit)
        rows = conn.execute(query, params).fetchall()

        n_found = 0
        for r in rows:
            text = strip_html(r["description"] or "")
            lo, hi, cur = extract_comp(text)
            if lo is None:
                continue
            conn.execute(
                "UPDATE jobs SET comp_min=?, comp_max=?, comp_currency=? WHERE id=?",
                (lo, hi, cur, r["id"]),
            )
            conn.commit()
            n_found += 1
            print(f"  [{r['id']:>6}] {r['title'][:60]:60s} ${lo:,.0f} - ${hi:,.0f} {cur}")

        print(f"\n{len(rows)} jobs checked, {n_found} backfilled with comp")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
