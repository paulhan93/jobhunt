"""Regression tests for pipeline.db.db_session(), the commit/rollback/close
contextmanager added to replace the inconsistent `with get_conn() as conn:`
pattern (a raw sqlite3.Connection context manager commits or rolls back the
transaction but never closes the connection, a well-known stdlib gotcha).
Uses a real temp-file sqlite database via monkeypatch on DB_PATH, never the
project's own jobs.db.
"""
import sqlite3

import pytest

import pipeline.db as db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    conn = db.get_conn()
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()
    return path


def _rows(path):
    conn = sqlite3.connect(path)
    rows = conn.execute("SELECT x FROM t").fetchall()
    conn.close()
    return rows


def test_commits_on_success(temp_db):
    with db.db_session() as conn:
        conn.execute("INSERT INTO t VALUES (1)")
    assert _rows(temp_db) == [(1,)]


def test_rolls_back_on_exception(temp_db):
    with pytest.raises(ValueError):
        with db.db_session() as conn:
            conn.execute("INSERT INTO t VALUES (1)")
            raise ValueError("simulated failure mid-transaction")
    assert _rows(temp_db) == []


def test_rolls_back_on_system_exit_not_just_exception(temp_db):
    # SystemExit/KeyboardInterrupt aren't Exception subclasses, a script
    # like log_application.py raises SystemExit for an expected early exit
    # ("job already applied"), and that must not leave a half-written row.
    with pytest.raises(SystemExit):
        with db.db_session() as conn:
            conn.execute("INSERT INTO t VALUES (1)")
            raise SystemExit("simulated expected early exit")
    assert _rows(temp_db) == []


def test_connection_is_actually_closed(temp_db):
    with db.db_session() as conn:
        pass
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
