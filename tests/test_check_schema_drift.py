"""Regression tests for scripts.check_schema_drift, itself the regression
guard added 2026-08-17 (DECISIONS.md #83) after schema.sql was found to
describe four indexes that didn't actually exist on the live database.
Builds two real scratch databases, never touches the project's actual
jobs.db, and never opens it even read-only (LIVE_DB is monkeypatched to a
scratch path for every test here).
"""
import sqlite3

import pytest

import scripts.check_schema_drift as csd

_SCHEMA = """
CREATE TABLE widgets (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    price REAL
);
CREATE INDEX idx_widgets_name ON widgets(name);
CREATE VIEW cheap_widgets AS SELECT * FROM widgets WHERE price < 10;
"""


@pytest.fixture
def schema_file(tmp_path, monkeypatch):
    path = tmp_path / "schema.sql"
    path.write_text(_SCHEMA)
    monkeypatch.setattr(csd, "SCHEMA_FILE", str(path))
    return path


def _live_db(tmp_path, monkeypatch, sql: str) -> str:
    path = str(tmp_path / "live.db")
    conn = sqlite3.connect(path)
    conn.executescript(sql)
    conn.close()
    monkeypatch.setattr(csd, "LIVE_DB", path)
    return path


def test_identical_schema_and_live_db_report_in_sync(tmp_path, monkeypatch, schema_file):
    _live_db(tmp_path, monkeypatch, _SCHEMA)
    assert csd.check() is True


def test_missing_index_on_live_db_is_detected(tmp_path, monkeypatch, schema_file, capsys):
    sql = _SCHEMA.replace("CREATE INDEX idx_widgets_name ON widgets(name);\n", "")
    _live_db(tmp_path, monkeypatch, sql)
    assert csd.check() is False
    assert "idx_widgets_name" in capsys.readouterr().out


def test_missing_column_on_live_db_is_detected(tmp_path, monkeypatch, schema_file, capsys):
    # No "price" column at all, so the view that depends on it is dropped too
    # (not the thing under test here, the missing column is).
    sql = """
CREATE TABLE widgets (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE INDEX idx_widgets_name ON widgets(name);
"""
    _live_db(tmp_path, monkeypatch, sql)
    assert csd.check() is False
    assert "price" in capsys.readouterr().out


def test_missing_table_on_live_db_is_detected(tmp_path, monkeypatch, schema_file, capsys):
    _live_db(tmp_path, monkeypatch, "")  # empty live database
    assert csd.check() is False
    assert "widgets" in capsys.readouterr().out


def test_differing_view_definition_is_detected(tmp_path, monkeypatch, schema_file, capsys):
    sql = _SCHEMA.replace("price < 10", "price < 5")  # same name, different SQL
    _live_db(tmp_path, monkeypatch, sql)
    assert csd.check() is False
    assert "cheap_widgets" in capsys.readouterr().out


def test_missing_live_db_reports_in_sync_nothing_to_compare(monkeypatch, schema_file):
    monkeypatch.setattr(csd, "LIVE_DB", "/nonexistent/path/does-not-exist.db")
    assert csd.check() is True
