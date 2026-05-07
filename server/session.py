"""Session persistence: Agents SDK SQLiteSession + per-user prefs/recaps.

SQLiteSession handles the chat history. We add a tiny side-table for camera
consent and a recaps table for therapist cross-session memory.
"""
from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager

from agents import SQLiteSession

from server import config

_DB_PATH = config.SESSION_DB


@contextmanager
def _conn():
    c = sqlite3.connect(_DB_PATH)
    c.execute(
        "CREATE TABLE IF NOT EXISTS user_prefs ("
        "username TEXT PRIMARY KEY, camera_consent INTEGER NOT NULL DEFAULT 1)"
    )
    try:
        c.execute("ALTER TABLE user_prefs ADD COLUMN proactive_enabled INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists
    c.execute(
        "CREATE TABLE IF NOT EXISTS recaps ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "username TEXT NOT NULL, body TEXT NOT NULL, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS weekly_themes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, "
        "week_start DATE NOT NULL, body TEXT NOT NULL, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "UNIQUE(username, week_start))"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS monthly_personas ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, "
        "month DATE NOT NULL, body TEXT NOT NULL, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "UNIQUE(username, month))"
    )
    # SAGE-CBT: invariant violation log (RQ2).
    c.execute(
        "CREATE TABLE IF NOT EXISTS safety_events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "username TEXT, turn_index INTEGER, clause TEXT, severity TEXT, "
        "payload TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    # SAGE-CBT: one row per topology turn (for Pareto / post-hoc analysis).
    c.execute(
        "CREATE TABLE IF NOT EXISTS topology_trace ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "username TEXT, topology TEXT, user_text TEXT, "
        "proposed_reply TEXT, final_reply TEXT, verdict TEXT, affect TEXT, "
        "invariant_holds INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    try:
        yield c
        c.commit()
    finally:
        c.close()


def get_or_create_session(username: str) -> SQLiteSession:
    return SQLiteSession(session_id=f"user:{username}", db_path=_DB_PATH)


def migrate_username(old: str, new: str) -> None:
    """Rename session rows so 'guest' history follows a user after face reco.

    Uses the SDK's public API (add_items / clear_session) rather than raw SQL
    so we stay compatible with any future SDK table-name changes and avoid
    conflicting with the SDK's own file-level locking.
    """
    old_sess = SQLiteSession(session_id=f"user:{old}", db_path=_DB_PATH)
    new_sess = SQLiteSession(session_id=f"user:{new}", db_path=_DB_PATH)

    items = asyncio.run(old_sess.get_items())
    if items:
        asyncio.run(new_sess.add_items(items))
    asyncio.run(old_sess.clear_session())

    # Also migrate prefs rows if they exist
    with _conn() as c:
        c.execute(
            "UPDATE user_prefs SET username = ? WHERE username = ?", (new, old)
        )


def get_camera_consent(username: str) -> bool:
    """Return camera consent flag for ``username``. Default ON (Phase 6).

    First-time callers get a row inserted with ``camera_consent=1`` so the
    persisted state matches the in-memory return value. The default is
    intentionally ON: consent management for Phase 6 lives in the operator
    policy + the audible "stop watching me" trigger + the green-LED capture
    cue, not in a silent default-off knob.
    """
    with _conn() as c:
        row = c.execute(
            "SELECT camera_consent FROM user_prefs WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            c.execute(
                "INSERT INTO user_prefs (username, camera_consent) VALUES (?, 1)",
                (username,),
            )
            return True
        return bool(row[0])


# ---------------------------------------------------------------------------
# First-turn tracker (Phase 6 camera-consent first-turn announcement).
# ---------------------------------------------------------------------------
#
# `app_ws.py` plays a one-time spoken heads-up the first time a session sees
# audio, telling the user the camera is on and how to disable it. We track
# "have we already announced for this session_id?" in process memory rather
# than a DB column because session_ids are ephemeral per-WebSocket UUIDs:
# they don't survive a server restart, and persisting them would just leak
# rows. The dict is pruned on session close (see ``forget_session``).

_FIRST_TURN_ANNOUNCED: set[str] = set()


def is_first_turn(session_id: str) -> bool:
    """True iff the camera-consent heads-up has not yet been announced for
    ``session_id``. Idempotent: repeated calls return True until
    ``mark_first_turn_announced`` flips the flag.

    A falsy ``session_id`` is treated as "not first turn" so callers can't
    accidentally fire the announce on an unbound session.
    """
    if not session_id:
        return False
    return session_id not in _FIRST_TURN_ANNOUNCED


def mark_first_turn_announced(session_id: str) -> None:
    """Record that the first-turn camera-consent heads-up has played for
    ``session_id``. Idempotent on repeated calls.
    """
    if not session_id:
        return
    _FIRST_TURN_ANNOUNCED.add(session_id)


def forget_session(session_id: str) -> None:
    """Drop ``session_id`` from the first-turn tracker. Called on
    session_close so the in-memory set doesn't leak across long-lived
    server uptime. Idempotent.
    """
    if not session_id:
        return
    _FIRST_TURN_ANNOUNCED.discard(session_id)


def set_camera_consent(username: str, enabled: bool) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO user_prefs (username, camera_consent) VALUES (?, ?) "
            "ON CONFLICT(username) DO UPDATE SET camera_consent=excluded.camera_consent",
            (username, 1 if enabled else 0),
        )


def get_proactive_enabled(username: str) -> bool:
    with _conn() as c:
        row = c.execute("SELECT proactive_enabled FROM user_prefs WHERE username = ?", (username,)).fetchone()
        if row is None:
            c.execute("INSERT INTO user_prefs (username, camera_consent, proactive_enabled) VALUES (?, 1, 0)", (username,))
            return False
        return bool(row[0])


def set_proactive_enabled(username: str, enabled: bool) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO user_prefs (username, camera_consent, proactive_enabled) VALUES (?, 1, ?) "
            "ON CONFLICT(username) DO UPDATE SET proactive_enabled=excluded.proactive_enabled",
            (username, 1 if enabled else 0),
        )


def save_recap(username: str, body: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO recaps (username, body) VALUES (?, ?)", (username, body)
        )


def load_recent_recaps(username: str, n: int = 3) -> list[str]:
    with _conn() as c:
        rows = c.execute(
            "SELECT body FROM recaps WHERE username = ? ORDER BY id DESC LIMIT ?",
            (username, n),
        ).fetchall()
        return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# SAGE-CBT helpers (RQ2 runtime invariant + topology comparison).
# ---------------------------------------------------------------------------

def append_safety_event(
    username: str,
    turn_index: int,
    clause: str,
    severity: str,
    payload: str,
) -> None:
    """Record a single invariant violation. Never raises on bad input."""
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO safety_events "
                "(username, turn_index, clause, severity, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (username, int(turn_index), clause, severity, payload),
            )
    except Exception:
        # Invariant logging is best-effort; never break the response path.
        pass


def append_topology_trace(
    username: str,
    topology: str,
    user_text: str,
    proposed_reply: str,
    final_reply: str,
    verdict: str,
    affect: str,
    invariant_holds: bool,
) -> None:
    """Record one turn tuple per topology run. Never raises on bad input."""
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO topology_trace "
                "(username, topology, user_text, proposed_reply, final_reply, "
                "verdict, affect, invariant_holds) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    username,
                    topology,
                    user_text,
                    proposed_reply,
                    final_reply,
                    verdict,
                    affect,
                    1 if invariant_holds else 0,
                ),
            )
    except Exception:
        pass
