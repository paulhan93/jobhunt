import json
from pathlib import Path

from pipeline.db import get_conn

RESULTS_FILE = Path("data/probe_results.json")


def main():
    results = json.loads(RESULTS_FILE.read_text())

    rows = [
        (name, r["ats"], r["slug"])
        for name, r in sorted(results.items())
        if r.get("status") in ("hit", "empty")
    ]

    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO companies (name, ats, slug) VALUES (?, ?, ?)
            ON CONFLICT (ats, slug) DO UPDATE SET name = excluded.name
            """,
            rows,
        )

        print(f"Loaded {len(rows)} companies.\n")
        for ats, n in conn.execute(
            "SELECT ats, COUNT(*) FROM companies WHERE active = 1 "
            "GROUP BY ats ORDER BY 2 DESC"
        ):
            print(f"  {ats:16} {n}")

    skipped = [n for n, r in results.items() if r.get("status") not in ("hit", "empty")]
    if skipped:
        print(f"\n{len(skipped)} unresolved:")
        for n in sorted(skipped):
            print(f"  {n}")


if __name__ == "__main__":
    main()
