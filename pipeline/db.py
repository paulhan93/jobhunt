import sqlite3
from contextlib import contextmanager

DB_PATH = "jobs.db"

def get_conn():
    # timeout=30: several scripts can legitimately hold jobs.db open at once
    # (cron's fetch_all, a long-running extract_all, a manual query), the
    # sqlite3 module's own 5s default is too short for that and fails fast
    # with "database is locked" instead of just waiting a beat.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db_session():
    """Commits on success, rolls back on exception, and always closes.

    A raw sqlite3.Connection used as `with get_conn() as conn:` only manages
    the transaction (commit/rollback), it does NOT close the connection,
    a well-known stdlib sqlite3 gotcha. For the short one-shot scripts this
    project runs, the OS reclaims the handle at process exit either way, so
    this was never a live bug, just an inconsistency (some scripts closed
    explicitly via try/finally, some didn't). Use this for a script that
    does one unit of work; a script that commits incrementally across a long
    loop (extract_all.py, score_all.py) should keep using get_conn() with
    its own try/finally, same as today."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except BaseException:
        # BaseException, not Exception: scripts using this (e.g.
        # log_application.py) raise SystemExit for an expected early exit
        # ("job already applied"), and a plain Ctrl-C is KeyboardInterrupt,
        # neither is an Exception subclass, and both deserve an explicit
        # rollback rather than relying on close()'s implicit one.
        conn.rollback()
        raise
    finally:
        conn.close()
