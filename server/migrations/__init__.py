"""Lightweight SQLite migration runner for ``server/nao.db``.

Phase 6 introduces the first migration (``0001_camera_default_on.py``)
to ensure the ``user_prefs.camera_consent`` column defaults to ``1``
for new rows. The runner is intentionally tiny: there are no plans for
heavy schema work — most tables are still created at connect time by
``server/session.py:_conn``. Migrations exist for column-default
changes (and similar one-shot fixes) that ``CREATE TABLE IF NOT EXISTS``
cannot retrofit.

Contract
--------
* Each migration file is named ``0NNN_<slug>.py`` (zero-padded 4-digit
  numeric prefix). Files in this directory that don't match are ignored.
* Each migration module exposes a top-level callable
  ``migrate(conn: sqlite3.Connection) -> None``. The callable MUST be
  idempotent (re-runs are a no-op).
* The runner records applied migrations in a ``migrations`` table so a
  given file is only ever executed once per database — but the
  idempotency requirement is the load-bearing safety net (the
  recorded-state check is just an optimization to skip already-applied
  files).

Usage
-----
``from server.migrations import apply_pending_migrations``
``apply_pending_migrations()`` — runs against ``config.SESSION_DB``.
``apply_pending_migrations(db_path=...)`` — point at a different DB
(used by tests with a tmp_path).

When ``config.CAMERA_DEFAULT_ON`` is False, ``0001_camera_default_on``
is intentionally skipped (defensive: matches main behavior). All other
migrations always run when pending.
"""
from __future__ import annotations

import importlib
import logging
import re
import sqlite3
from pathlib import Path
from typing import Iterable

from server import config

_log = logging.getLogger("sage.migrations")

_MIGRATION_RE = re.compile(r"^(?P<num>\d{4})_(?P<slug>[A-Za-z0-9_]+)\.py$")

# Migrations gated by an env/config knob. If the knob is falsy, the
# corresponding migration is skipped (and not recorded), so flipping
# the knob to True later still applies the migration.
_GATED_MIGRATIONS: dict[str, tuple[str, bool]] = {
    "0001_camera_default_on": ("CAMERA_DEFAULT_ON", True),
}


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    """Create the `migrations` ledger table if it doesn't already exist."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS migrations ("
        "name TEXT PRIMARY KEY, "
        "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )


def _already_applied(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM migrations WHERE name = ?", (name,)
    ).fetchone()
    return row is not None


def _record(conn: sqlite3.Connection, name: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO migrations (name) VALUES (?)", (name,)
    )


def _discover() -> list[str]:
    """Return migration module names sorted by numeric prefix."""
    here = Path(__file__).resolve().parent
    names: list[str] = []
    for entry in here.iterdir():
        if not entry.is_file():
            continue
        m = _MIGRATION_RE.match(entry.name)
        if not m:
            continue
        names.append(entry.stem)
    names.sort()
    return names


def _is_gated_off(name: str) -> bool:
    """Return True if `name` is gated by a config knob that's currently
    set to a falsy value. Unknown names are always allowed."""
    if name not in _GATED_MIGRATIONS:
        return False
    attr, default = _GATED_MIGRATIONS[name]
    return not bool(getattr(config, attr, default))


def apply_pending_migrations(
    db_path: str | None = None,
    names: Iterable[str] | None = None,
) -> list[str]:
    """Apply any migration files that haven't been applied to ``db_path``.

    Returns the list of migration names that were freshly applied this
    call. Idempotent: a second invocation is a no-op once everything is
    recorded.

    ``db_path`` defaults to ``config.SESSION_DB``. ``names`` lets tests
    override discovery to apply a specific subset.
    """
    path = db_path or config.SESSION_DB
    discovered = list(names) if names is not None else _discover()
    applied_now: list[str] = []
    if not discovered:
        return applied_now

    conn = sqlite3.connect(path)
    try:
        _ensure_migrations_table(conn)
        for name in discovered:
            if _is_gated_off(name):
                _log.debug("migrations.skip_gated", extra={"migration": name})
                continue
            if _already_applied(conn, name):
                continue
            module_name = f"server.migrations.{name}"
            try:
                module = importlib.import_module(module_name)
            except Exception as e:  # noqa: BLE001
                _log.error(
                    "migrations.import_failed name=%s error=%r",
                    name, e,
                )
                continue
            migrate_fn = getattr(module, "migrate", None)
            if not callable(migrate_fn):
                _log.error(
                    "migrations.missing_migrate_callable name=%s", name,
                )
                continue
            try:
                migrate_fn(conn)
                _record(conn, name)
                conn.commit()
                applied_now.append(name)
                _log.info("migrations.applied name=%s", name)
            except Exception as e:  # noqa: BLE001
                conn.rollback()
                _log.error(
                    "migrations.apply_failed name=%s error=%r",
                    name, e,
                )
        return applied_now
    finally:
        conn.close()
