"""0001 — flip the ``user_prefs.camera_consent`` column default to ``1``.

Phase 6 makes camera consent default-ON. New rows must persist
``camera_consent = 1`` even when an INSERT omits the column. Existing
rows are left untouched (operator policy: don't quietly re-enable the
camera for users who turned it off in earlier sessions).

SQLite quirks
-------------
SQLite cannot ``ALTER COLUMN`` to change a column default. The portable
fix is the rename/recreate/copy/drop pattern: build a fresh table with
the new default, copy rows over verbatim, drop the old, rename the new
into place. We wrap the whole thing in a transaction so a failure leaves
the original table in place.

Idempotency
-----------
The migration runner records this file in the ``migrations`` ledger so
it only ever fires once per database. As a defense-in-depth, we also
inspect the table's stored DDL via ``sqlite_master`` before doing any
work — if the existing definition already includes
``camera_consent INTEGER NOT NULL DEFAULT 1`` (current ``session.py``
emits exactly that), we skip the rebuild entirely.
"""
from __future__ import annotations

import logging
import re
import sqlite3

_log = logging.getLogger("sage.migrations.0001")

# Regex that matches "camera_consent ... DEFAULT 1" inside the stored DDL.
# We don't try to be too strict — sqlite normalizes whitespace but
# preserves literal column ordering; the DEFAULT clause we want is the
# only one that matters here.
_DEFAULT_ONE_RE = re.compile(
    r"camera_consent\s+INTEGER[^,]*DEFAULT\s+1",
    re.IGNORECASE,
)


def _user_prefs_ddl(conn: sqlite3.Connection) -> str | None:
    """Return the ``CREATE TABLE`` SQL for ``user_prefs`` from
    sqlite_master, or None if the table doesn't exist yet.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'user_prefs'"
    ).fetchone()
    if row is None:
        return None
    return row[0] or ""


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def migrate(conn: sqlite3.Connection) -> None:
    """Recreate ``user_prefs`` with ``camera_consent`` DEFAULT 1.

    Existing row values are preserved verbatim. No-op when the table
    already advertises the desired default or when the table doesn't
    exist yet (the next call to ``server.session._conn`` will create it
    with the modern schema).
    """
    ddl = _user_prefs_ddl(conn)

    # Table doesn't exist yet → nothing to migrate. The runtime
    # ``CREATE TABLE IF NOT EXISTS`` in session.py already emits the
    # correct DEFAULT 1 on first connect.
    if ddl is None:
        _log.debug("0001: user_prefs absent, skipping (will be created at runtime)")
        return

    # Default is already 1 → nothing to do.
    if _DEFAULT_ONE_RE.search(ddl):
        _log.debug("0001: user_prefs.camera_consent already DEFAULT 1, skipping")
        return

    cols = _column_names(conn, "user_prefs")
    if "camera_consent" not in cols:
        _log.warning(
            "0001: user_prefs has no camera_consent column "
            "(unexpected schema); skipping",
        )
        return
    if "username" not in cols:
        _log.warning(
            "0001: user_prefs has no username column "
            "(unexpected schema); skipping",
        )
        return

    has_proactive = "proactive_enabled" in cols
    new_table_cols = [
        "username TEXT PRIMARY KEY",
        "camera_consent INTEGER NOT NULL DEFAULT 1",
    ]
    select_cols = ["username", "camera_consent"]
    if has_proactive:
        new_table_cols.append("proactive_enabled INTEGER NOT NULL DEFAULT 0")
        select_cols.append("proactive_enabled")

    create_sql = (
        "CREATE TABLE user_prefs__new ("
        + ", ".join(new_table_cols)
        + ")"
    )
    insert_sql = (
        f"INSERT INTO user_prefs__new ({', '.join(select_cols)}) "
        f"SELECT {', '.join(select_cols)} FROM user_prefs"
    )

    # Wrap rebuild in a savepoint so a partial failure rolls back cleanly
    # without disturbing any work the caller did before invoking us.
    conn.execute("SAVEPOINT migrate_0001")
    try:
        conn.execute("DROP TABLE IF EXISTS user_prefs__new")
        conn.execute(create_sql)
        conn.execute(insert_sql)
        conn.execute("DROP TABLE user_prefs")
        conn.execute("ALTER TABLE user_prefs__new RENAME TO user_prefs")
        conn.execute("RELEASE SAVEPOINT migrate_0001")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT migrate_0001")
        conn.execute("RELEASE SAVEPOINT migrate_0001")
        raise

    _log.info("0001: user_prefs rebuilt with camera_consent DEFAULT 1")
