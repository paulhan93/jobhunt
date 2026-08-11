import json
import time
from pathlib import Path

import httpx

from pipeline.ats import (
    ATS_PATTERNS, PROBE_ORDER, STRICT_404, count_jobs, slug_candidates
)

NAMES_FILE = Path("data/companies.txt")
RESULTS_FILE = Path("data/probe_results.json")
HEADERS = {"User-Agent": "jobhunt-probe/0.1 (paul@example.com)"}
SLEEP = 0.4


def probe_one(client, ats: str, slug: str) -> dict:
    """Try one (ats, slug) pair. Returns a verdict; never raises."""
    url = ATS_PATTERNS[ats].format(slug=slug)
    try:
        r = client.get(url, headers=HEADERS, timeout=10.0)
    except httpx.RequestError as e:
        return {"status": "error", "reason": type(e).__name__}

    if r.status_code == 429:
        return {"status": "error", "reason": "rate limited"}
    if r.status_code != 200:
        return {"status": "miss", "reason": f"http {r.status_code}"}

    try:
        data = r.json()
    except ValueError:
        return {"status": "miss", "reason": "not json"}

    n = count_jobs(ats, data)
    if n > 0:
        return {"status": "hit", "count": n}
    if ats in STRICT_404:
        return {"status": "empty", "count": 0}
    return {"status": "miss", "reason": "200 but no jobs (slug likely invalid)"}


def main():
    names = [ln.split("#")[0].strip() for ln in NAMES_FILE.read_text().splitlines()]
    names = [n for n in names if n]

    results = {}
    if RESULTS_FILE.exists():
        results = json.loads(RESULTS_FILE.read_text())

    with httpx.Client(follow_redirects=True) as client:
        for name in names:
            if results.get(name, {}).get("status") in ("hit", "empty", "no_public_ats"):
                print(f"  skip  {name}")
                continue

            found = None
            for ats in PROBE_ORDER:
                for slug in slug_candidates(name):
                    verdict = probe_one(client, ats, slug)
                    time.sleep(SLEEP)
                    if verdict["status"] in ("hit", "empty"):
                        found = {"ats": ats, "slug": slug, **verdict}
                        break
                if found:
                    break

            results[name] = found or {"status": "miss"}
            RESULTS_FILE.write_text(json.dumps(results, indent=2, sort_keys=True))

            r = results[name]
            if r["status"] == "hit":
                print(f"  HIT   {name}: {r['ats']}/{r['slug']} ({r['count']} jobs)")
            elif r["status"] == "empty":
                print(f"  EMPTY {name}: {r['ats']}/{r['slug']} (board exists, 0 open)")
            else:
                print(f"  MISS  {name}")

    resolved = sum(1 for r in results.values() if r["status"] in ("hit", "empty"))
    print(f"\n{resolved}/{len(names)} resolved")


if __name__ == "__main__":
    main()
