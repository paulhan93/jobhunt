"""Checks for drift between schema.sql (what a fresh database gets via
reset.sh) and the live jobs.db (what's actually running). Read-only
against jobs.db, opened via a `mode=ro` URI so it's impossible for this
script to write to it even by accident.

Motivated by a real gap found 2026-08-17 (DECISIONS.md #77): schema.sql
documented four indexes that had never actually existed on the live
database, undiscovered until an unrelated performance investigation
stumbled onto it by accident. This script exists so the next drift of that
shape gets caught by running one command, not by luck.

Deliberately does NOT diff full table CREATE-statement text: SQLite's
ALTER TABLE ADD COLUMN cannot add a CHECK constraint, a documented,
accepted gap throughout migrations/ (e.g. jobs.role_family/fit_tier), so
the live database's stored table SQL differs from schema.sql's on that
dimension by design. Diffing full table text would just be noise. Indexes
and views ARE fully replaced (CREATE INDEX, DROP+CREATE VIEW), not
incrementally ALTER'd, so their SQL text is compared directly, that
dimension is meaningful, not noise.

Exit code 0 if no drift, 1 if drift found, shell-script/cron-friendly.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

LIVE_DB = "jobs.db"
SCHEMA_FILE = "schema.sql"


def _objects(conn: sqlite3.Connection, obj_type: str) -> dict[str, str]:
    """{name: normalized_sql} for user objects of one type (sqlite_'s own
    internal bookkeeping tables/indexes excluded)."""
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'",
        (obj_type,),
    ).fetchall()
    return {name: " ".join((sql or "").split()) for name, sql in rows}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def check() -> bool:
    """Prints any drift found. Returns True if the two are in sync."""
    if not Path(LIVE_DB).exists():
        print(f"{LIVE_DB} doesn't exist yet, nothing to compare against.")
        return True

    with tempfile.TemporaryDirectory() as tmp:
        fresh = sqlite3.connect(str(Path(tmp) / "fresh.db"))
        fresh.executescript(Path(SCHEMA_FILE).read_text())

        live = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)

        in_sync = True

        fresh_tables = _objects(fresh, "table")
        live_tables = _objects(live, "table")
        for label, missing in (
            (f"table(s) in {SCHEMA_FILE} but MISSING from {LIVE_DB}",
             set(fresh_tables) - set(live_tables)),
            (f"table(s) in {LIVE_DB} but not in {SCHEMA_FILE}",
             set(live_tables) - set(fresh_tables)),
        ):
            if missing:
                in_sync = False
                print(f"{label}: {sorted(missing)}")

        for table in sorted(set(fresh_tables) & set(live_tables)):
            fresh_cols = _columns(fresh, table)
            live_cols = _columns(live, table)
            for label, missing in (
                (f"{table}: column(s) in {SCHEMA_FILE} but MISSING from {LIVE_DB}",
                 fresh_cols - live_cols),
                (f"{table}: column(s) in {LIVE_DB} but not in {SCHEMA_FILE}",
                 live_cols - fresh_cols),
            ):
                if missing:
                    in_sync = False
                    print(f"{label}: {sorted(missing)}")

        for obj_type in ("index", "view"):
            fresh_objs = _objects(fresh, obj_type)
            live_objs = _objects(live, obj_type)
            for label, missing in (
                (f"{obj_type}(s) in {SCHEMA_FILE} but MISSING from {LIVE_DB}",
                 set(fresh_objs) - set(live_objs)),
                (f"{obj_type}(s) in {LIVE_DB} but not in {SCHEMA_FILE}",
                 set(live_objs) - set(fresh_objs)),
            ):
                if missing:
                    in_sync = False
                    print(f"{label}: {sorted(missing)}")
            for name in sorted(set(fresh_objs) & set(live_objs)):
                if fresh_objs[name] != live_objs[name]:
                    in_sync = False
                    print(f"{obj_type} '{name}' definition differs between "
                          f"{SCHEMA_FILE} and {LIVE_DB}:")
                    print(f"  {SCHEMA_FILE}: {fresh_objs[name]}")
                    print(f"  {LIVE_DB}: {live_objs[name]}")

        fresh.close()
        live.close()

    if in_sync:
        print(f"No drift: {SCHEMA_FILE} and {LIVE_DB} agree on every table, "
              f"column, index, and view.")
    return in_sync


if __name__ == "__main__":
    sys.exit(0 if check() else 1)
