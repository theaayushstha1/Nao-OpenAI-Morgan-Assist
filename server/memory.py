"""Per-face persistent memory: users, sessions, rolling profiles.

Lives alongside SQLiteSession (Agents SDK) in the same DB at config.SESSION_DB.
SQLiteSession owns chat history; this module owns the "who is this human" layer.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from openai import OpenAI

from server import config

_DB_PATH = config.SESSION_DB
_SUMMARY_MODEL = "gpt-4.1-nano"
_client = OpenAI(api_key=config.OPENAI_API_KEY)


# ───────── schema ─────────

@contextmanager
def _conn():
    c = sqlite3.connect(_DB_PATH)
    # Foreign keys off by default in sqlite; turn on so cascade-ish deletes
    # behave intuitively even though we delete by hand in forget_user.
    c.execute("PRAGMA foreign_keys = ON")
    c.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "face_id TEXT PRIMARY KEY, "
        "display_name TEXT, "
        "profile_json TEXT NOT NULL DEFAULT '{}', "
        "created_at REAL NOT NULL, "
        "updated_at REAL NOT NULL)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "face_id TEXT NOT NULL, "
        "mode TEXT, "
        "started_at REAL NOT NULL, "
        "ended_at REAL, "
        "summary TEXT, "
        "FOREIGN KEY (face_id) REFERENCES users(face_id))"
    )
    # Hot path: recent_sessions filters by face_id and orders by id desc.
    # Index on face_id covers the WHERE; the PK index handles the ORDER BY.
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_face_id "
        "ON sessions(face_id)"
    )
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _norm(face_id: str) -> str:
    return (face_id or "").strip().lower()


# ───────── users ─────────

def ensure_user(face_id: str, display_name: str | None = None) -> None:
    """Upsert a user row. Updates display_name if provided and changed."""
    fid = _norm(face_id)
    if not fid:
        return
    now = time.time()
    with _conn() as c:
        row = c.execute(
            "SELECT display_name FROM users WHERE face_id = ?", (fid,)
        ).fetchone()
        if row is None:
            c.execute(
                "INSERT INTO users (face_id, display_name, profile_json, created_at, updated_at) "
                "VALUES (?, ?, '{}', ?, ?)",
                (fid, display_name, now, now),
            )
        elif display_name and display_name != row[0]:
            c.execute(
                "UPDATE users SET display_name = ?, updated_at = ? WHERE face_id = ?",
                (display_name, now, fid),
            )


def get_profile(face_id: str) -> dict:
    fid = _norm(face_id)
    with _conn() as c:
        row = c.execute(
            "SELECT profile_json FROM users WHERE face_id = ?", (fid,)
        ).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row[0]) or {}
    except (TypeError, ValueError):
        return {}


def update_profile(face_id: str, updates: dict) -> dict:
    """Shallow-merge updates into profile_json. Returns the new profile."""
    fid = _norm(face_id)
    if not fid or not isinstance(updates, dict):
        return {}
    ensure_user(fid)
    now = time.time()
    with _conn() as c:
        row = c.execute(
            "SELECT profile_json FROM users WHERE face_id = ?", (fid,)
        ).fetchone()
        try:
            current = json.loads(row[0]) if row and row[0] else {}
        except (TypeError, ValueError):
            current = {}
        if not isinstance(current, dict):
            current = {}
        current.update(updates)
        c.execute(
            "UPDATE users SET profile_json = ?, updated_at = ? WHERE face_id = ?",
            (json.dumps(current), now, fid),
        )
        return current


def forget_user(face_id: str) -> None:
    """Delete user, their sessions, and their SQLiteSession history."""
    fid = _norm(face_id)
    if not fid:
        return
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE face_id = ?", (fid,))
        c.execute("DELETE FROM users WHERE face_id = ?", (fid,))
    # Wipe Agents SDK chat history too.
    try:
        from server import session as _session
        import asyncio
        sess = _session.get_or_create_session(fid)
        asyncio.run(sess.clear_session())
    except Exception:
        pass


# ───────── sessions ─────────

def start_session(face_id: str, mode: str | None = None) -> int:
    fid = _norm(face_id)
    ensure_user(fid)
    now = time.time()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO sessions (face_id, mode, started_at) VALUES (?, ?, ?)",
            (fid, mode, now),
        )
        return int(cur.lastrowid)


def end_session(session_id: int, summary: str | None = None) -> None:
    if not session_id:
        return
    now = time.time()
    with _conn() as c:
        if summary is None:
            c.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?", (now, session_id)
            )
        else:
            c.execute(
                "UPDATE sessions SET ended_at = ?, summary = ? WHERE id = ?",
                (now, summary, session_id),
            )


def recent_sessions(face_id: str, n: int = 3) -> list[dict]:
    fid = _norm(face_id)
    with _conn() as c:
        rows = c.execute(
            "SELECT id, mode, started_at, ended_at, summary "
            "FROM sessions WHERE face_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (fid, int(n)),
        ).fetchall()
    return [
        {"id": r[0], "mode": r[1], "started_at": r[2],
         "ended_at": r[3], "summary": r[4]}
        for r in rows
    ]


# ───────── async summarization ─────────

def summarize_session_async(session_id: int, transcript_lines: list[str]) -> None:
    """Kick off LLM summary in a daemon thread. Non-blocking."""
    if not session_id or not transcript_lines:
        return

    def _worker():
        try:
            transcript = "\n".join(s for s in transcript_lines if s)
            if not transcript.strip():
                return
            resp = _client.chat.completions.create(
                model=_SUMMARY_MODEL,
                temperature=0.2,
                messages=[
                    {"role": "system",
                     "content": ("Summarize this conversation between a user and "
                                 "NAO (a robot assistant) in 2-3 short sentences. "
                                 "Focus on topics discussed and any commitments or "
                                 "feelings the user expressed. Past tense, third person.")},
                    {"role": "user", "content": transcript[:8000]},
                ],
            )
            summary = (resp.choices[0].message.content or "").strip()
            if not summary:
                return
            with _conn() as c:
                c.execute(
                    "UPDATE sessions SET summary = ? WHERE id = ?",
                    (summary, session_id),
                )
        except Exception as e:  # noqa: BLE001
            print("[memory.summarize] failed: {0!r}".format(e), flush=True)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


# ───────── context preamble ─────────

def _format_age(started_at: float, now: float) -> str:
    if not started_at:
        return "recently"
    delta = max(0.0, now - started_at)
    days = delta / 86400.0
    if days < 1:
        return "earlier today"
    if days < 2:
        return "yesterday"
    return "{0} days ago".format(int(days))


def build_context_preamble(face_id: str) -> str:
    """Return a one-liner system note about this user's recent history.

    Empty string for new users or if anything fails — never raises.
    """
    try:
        fid = _norm(face_id)
        if not fid:
            return ""
        with _conn() as c:
            row = c.execute(
                "SELECT display_name FROM users WHERE face_id = ?", (fid,)
            ).fetchone()
        if not row:
            return ""
        display = row[0] or fid
        sessions = recent_sessions(fid, n=3)
        sessions = [s for s in sessions if s.get("summary")]
        if not sessions:
            return ""
        now = time.time()
        parts = []
        for s in sessions:
            age = _format_age(s.get("started_at") or 0.0, now)
            parts.append("{0}: {1}".format(age, s["summary"].strip()))
        return "[Returning user: {0}. Recent sessions: {1}]".format(
            display, " | ".join(parts)
        )
    except Exception:
        return ""
