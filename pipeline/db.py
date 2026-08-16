import sqlite3

DB_PATH = "jobs.db"

def get_conn():
    # timeout=30: several scripts can legitimately hold jobs.db open at once
    # (cron's fetch_all, a long-running extract_all, a manual query) — the
    # sqlite3 module's own 5s default is too short for that and fails fast
    # with "database is locked" instead of just waiting a beat.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn
