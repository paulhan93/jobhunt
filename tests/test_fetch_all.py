"""Regression test for scripts.fetch_all._write_company(): upsert, closed-
detection, and DB-failure isolation, the exact logic the 2026-08-17 real-
fault fix (DECISIONS.md #77) touched. This is the test that fix was
verified with during that session, run once by hand and never saved,
now permanent.

Builds a real scratch SQLite database from schema.sql, never touches the
project's actual jobs.db.
"""
import sqlite3

import pytest

import pipeline.db as db
import scripts.fetch_all as fetch_all
from pipeline.models import NormalizedJob


@pytest.fixture
def scratch_db(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    conn = sqlite3.connect(path)
    conn.executescript(open("schema.sql").read())
    conn.execute(
        "INSERT INTO companies (name, ats, slug) VALUES (?, ?, ?)",
        ("TestCo", "greenhouse", "testco"),
    )
    conn.commit()
    conn.close()

    conn = db.get_conn()
    company = conn.execute(
        "SELECT id, name, ats, slug FROM companies WHERE name = 'TestCo'"
    ).fetchone()
    yield conn, company
    conn.close()


def _job(job_id: str, title="Senior Software Engineer") -> NormalizedJob:
    return NormalizedJob(
        source="greenhouse", source_job_id=job_id, title=title,
        location="Remote, US", remote=True, description="desc",
        apply_url="http://example.com", raw={"id": job_id},
    )


def test_write_company_inserts_new_jobs(scratch_db):
    conn, company = scratch_db
    inserted, closed = fetch_all._write_company(conn, company, [_job("1"), _job("2")])
    conn.commit()
    assert (inserted, closed) == (2, 0)


def test_write_company_detects_a_closed_job(scratch_db):
    # Closed detection needs enough open jobs to clear the 70% truncation
    # guard (fetch_all.py's own protection against a partial API response
    # mass-closing live jobs), 5 jobs then 4 of the same 5 is safely above it.
    conn, company = scratch_db
    jobs = [_job(str(i)) for i in range(5)]
    fetch_all._write_company(conn, company, jobs)
    conn.commit()

    inserted, closed = fetch_all._write_company(conn, company, jobs[:4])  # job "4" gone
    conn.commit()
    assert (inserted, closed) == (0, 1)

    rows = dict(conn.execute(
        "SELECT source_job_id, closed_at IS NOT NULL FROM jobs"
    ).fetchall())
    assert rows == {"0": False, "1": False, "2": False, "3": False, "4": True}


def test_write_company_skips_closed_detection_below_the_truncation_guard(scratch_db):
    # A response with far fewer jobs than what's on file looks like a
    # truncated/broken API response, not real closures, must not mass-close.
    conn, company = scratch_db
    jobs = [_job(str(i)) for i in range(5)]
    fetch_all._write_company(conn, company, jobs)
    conn.commit()

    inserted, closed = fetch_all._write_company(conn, company, jobs[:1])  # 1 of 5, well below 70%
    conn.commit()
    assert closed == 0

    still_open = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE closed_at IS NULL"
    ).fetchone()[0]
    assert still_open == 5  # nothing closed


def test_write_company_upsert_is_idempotent(scratch_db):
    conn, company = scratch_db
    fetch_all._write_company(conn, company, [_job("1")])
    conn.commit()
    inserted, closed = fetch_all._write_company(conn, company, [_job("1")])
    conn.commit()
    assert inserted == 0  # already exists, no new row

    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert total == 1


def test_write_company_failure_does_not_corrupt_the_transaction(scratch_db):
    # This is the actual bug the 2026-08-17 fix addresses: one company's DB
    # failure must be isolated (caught, rolled back) by the caller without
    # losing any other company's already-committed work. This test exercises
    # _write_company's half of that contract: a failure partway through
    # raises cleanly rather than silently leaving a half-written state that
    # a subsequent conn.commit() would persist.
    conn, company = scratch_db
    fetch_all._write_company(conn, company, [_job("1")])
    conn.commit()

    bad_job = NormalizedJob(
        source="greenhouse", source_job_id=None, title="Broken",  # NOT NULL violation
        location=None, remote=None, description=None, apply_url=None, raw={},
    )
    with pytest.raises(sqlite3.IntegrityError):
        fetch_all._write_company(conn, company, [bad_job])
    conn.rollback()

    # The earlier, successfully committed job must have survived.
    total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert total == 1
