import argparse
import re

from pipeline.db import get_conn
from pipeline.render import render_resume
from pipeline.tailor import build_resume_doc, load_resume, reword_diffs, tailor_resume


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unknown"


def main():
    ap = argparse.ArgumentParser(
        description="Tailor and render a resume PDF for one job (step 7, "
                     "PROJECT.md §8). One job at a time by design — a batch "
                     "mode is backlogged until this flow is proven."
    )
    ap.add_argument("job_id", type=int)
    ap.add_argument("--out", default="output/resumes", help="output directory")
    args = ap.parse_args()

    with get_conn() as conn:
        job = conn.execute(
            """SELECT j.id, j.title, j.role_family, j.fit_score, j.fit_tier,
                      c.name AS company
               FROM jobs j LEFT JOIN companies c ON c.id = j.company_id
               WHERE j.id = ?""",
            (args.job_id,),
        ).fetchone()
        if job is None:
            raise SystemExit(f"no job with id {args.job_id}")
        if job["fit_tier"] is None:
            raise SystemExit(
                f"job {args.job_id} hasn't been scored yet — run extract_all.py "
                f"and score_all.py first"
            )

        requirements = conn.execute(
            "SELECT kind, text, matched_bullets FROM requirements WHERE job_id = ?",
            (args.job_id,),
        ).fetchall()

    resume = load_resume()
    print(f"tailoring for [{job['id']}] {job['title']} @ {job['company']} "
          f"({job['fit_tier']}, {job['fit_score']})\n")

    tailored = tailor_resume(job, requirements, resume)

    diffs = reword_diffs(tailored, resume)
    if diffs:
        print(f"{len(diffs)} reworded bullet(s) — review before sending:")
        for bid, before, after in diffs:
            print(f"\n  [{bid}]")
            print(f"  - {before}")
            print(f"  + {after}")
        print()

    doc = build_resume_doc(job, tailored, resume)
    if doc["summary"] is None:
        print(f"no pivot from '{job['role_family']}' — summary omitted\n")

    basename = f"{job['id']}_{_slug(job['company'] or 'unknown')}"
    pdf_path = render_resume(doc, args.out, basename)

    print(f"selected {len(tailored.selected_bullets)} bullets across "
          f"{len(doc['experience'])} role(s), {len(doc['projects'])} project(s), "
          f"skills: {', '.join(s['label'] for s in doc['skills'])}")
    print(f"\nwrote {pdf_path}")
    print(f"review the PDF, then if you send it:\n"
          f"  python -m scripts.log_application {job['id']} "
          f"--resume-version {basename}")


if __name__ == "__main__":
    main()
