"""Shared test fixtures. Kept here, not copy-pasted per test file, for the
same reason the source code itself shouldn't duplicate logic, a fixture
used by two test files should have one definition.
"""
import sqlite3

import pytest


def make_job_row(**overrides) -> sqlite3.Row:
    """A fake `jobs` row shaped like what pipeline.filters.classify() and
    pipeline.score.score_job() actually read, just the columns they touch,
    built via a real in-memory sqlite3.Row so tests exercise the exact same
    row type (with the same `row["key"]` access pattern) the real pipeline
    uses, not a plain dict standing in for one."""
    row = {
        "title": "", "location": None, "remote": None,
        "comp_min": None, "comp_max": None,
    }
    row.update(overrides)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"CREATE TABLE t ({cols})")
    conn.execute(f"INSERT INTO t VALUES ({placeholders})", tuple(row.values()))
    result = conn.execute(f"SELECT {cols} FROM t").fetchone()
    conn.close()
    return result


@pytest.fixture
def job():
    """Use as `job(title=..., location=...)`, a factory fixture, not a
    single fixed row, since nearly every test needs different fields set."""
    return make_job_row
