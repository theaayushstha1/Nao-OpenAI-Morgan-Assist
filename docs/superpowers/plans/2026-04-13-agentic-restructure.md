# Agentic Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the Flask server onto the OpenAI Agents SDK with a multi-agent graph (router + chat, chatbot, therapist with CBT/grounding sub-agents, skills); consolidate the NAO client from four mode files to one `conversation.py`; add multimodal emotion via GPT-4o vision; delete ~1400 lines of dead/duplicated code.

**Architecture:** Two processes joined by HTTP — NAO (Py 2.7, naoqi) sends multipart `audio + image + username + hint` to a single `POST /turn`. Server (Py 3.11+) runs the Agents SDK `Runner` with a `SQLiteSession`, returning `{reply, actions[], suppress_image, crisis}`. Agent tools split into *NAO action tools* (captured into a context-scoped `actions_queue` for NAO to execute) and *data tools* (pinecone, emotion, skills) that execute on the server.

**Tech Stack:** `openai-agents` (Agents SDK), `openai>=1.50`, `pinecone-client`, `flask`, `python-dotenv`, SQLite (via SDK). NAO side: `naoqi` (Py 2.7), `requests`.

**Companion spec:** `docs/superpowers/specs/2026-04-13-agentic-restructure-design.md` (read it before touching tasks).

**Branch:** Create `refactor/agents-sdk` off `refactor/openai-upgrade-and-cleanup`. Tag `7ff21dd` as `pre-agents-sdk` before starting Task 1.

---

## File Structure

### Server (Python 3, new `server/` package)

| File | Responsibility |
|------|----------------|
| `server/server.py` | Flask app, `POST /turn`, `GET /health`. Orchestrates validate → transcribe → crisis → runner → respond. |
| `server/config.py` | Env-driven config (model names, Pinecone, IPs, SQLite path, tracing). |
| `server/safety.py` | Pre-dispatch `crisis_check(text)` — keyword gate + `gpt-4o-mini` classifier. |
| `server/session.py` | `get_or_create_session`, `migrate_username`, `load_recent_recaps`, `save_recap`, `set_camera_consent`. |
| `server/agents/__init__.py` | Exports `build_agent_graph()` that wires handoffs. |
| `server/agents/router.py` | Triage agent. |
| `server/agents/chat.py` | Chat specialist (NAO actions only). |
| `server/agents/chatbot.py` | Morgan CS RAG (pinecone_search tool). |
| `server/agents/therapist.py` | Empathetic + CBT/grounding handoffs + emotion tools. |
| `server/agents/cbt_coach.py` | Thought-record walker. |
| `server/agents/grounding_coach.py` | 5-4-3-2-1, box breathing, body scan. |
| `server/agents/skills.py` | Time, weather, timers, reminders, todos. |
| `server/tools/__init__.py` | Re-exports. |
| `server/tools/nao_actions.py` | 18 NAO action tools; each appends to `context["actions_queue"]`. |
| `server/tools/pinecone_search.py` | Top-k query. |
| `server/tools/emotion.py` | `observe_face`, `log_emotion`, `identify_distortion`, `suggest_reframe`, `set_camera_consent`, `recap_session`, `set_led_color`. |
| `server/tools/skills_tools.py` | Time/weather/timer/reminder/todo tools. |
| `server/tools/session_tools.py` | `load_recent_recaps` (system-prompt helper, not a tool) + reserved. |
| `server/requirements.txt` | Pinned deps. |
| `server/tests/` | Pytest tree (`test_safety.py`, `test_session.py`, `test_tools.py`, `test_agents.py`, `test_turn_endpoint.py`). |

### NAO (Python 2.7, repo root)

| File | Responsibility |
|------|----------------|
| `main.py` | Entry; wake loop → `conversation.run(hint)`. |
| `wake_listener.py` | Wake phrase detection + hint extraction (`chat`/`morgan`/`therapy`/`skills`/`None`). |
| `conversation.py` | **NEW.** Single loop: record → (snap?) → POST → parse → speak + execute actions → exit. |
| `audio_handler.py` | Unchanged. |
| `processing_announcer.py` | Unchanged. |
| `config.py` | Unchanged. |
| `utils/camera_capture.py` | + `snap_quick()` (640×480 JPEG at VAD onset). |
| `utils/nao_execute.py` | **NEW.** `run({name, args})` → dispatch to naoqi calls. |
| `utils/face_naoqi.py` | Unchanged. |
| `utils/ask_name_utils.py` | Unchanged. |
| `utils/exit_detection.py` | Unchanged. |
| `utils/name_utils.py` | Unchanged. |
| `utils/speech.py` | Unchanged. |

### Deleted (Phase 8)

- `chat_mode.py`, `chatbot_mode.py`, `therapist_mode.py`, `mini_nao.py`
- `gpt_handler.py`, `memory_manager.py`, `face_store.py`, `memory.json`
- `utils/face_utils.py`, `utils/with_announcer.py`, `utils/file_utils.py`
- Old root-level `server.py` is replaced by `server/server.py` (git move with rewrite)

---

## Phase 0: Branch & tag

### Task 0.1: Create branch and rollback tag

**Files:** none (git only)

- [ ] **Step 1: Create rollback tag on the pre-refactor commit**

```bash
git tag pre-agents-sdk 7ff21dd
```

- [ ] **Step 2: Create the feature branch**

```bash
git checkout -b refactor/agents-sdk
```

- [ ] **Step 3: Verify**

```bash
git log --oneline -3
git tag --list 'pre-*'
```
Expected: branch `refactor/agents-sdk` checked out, tag `pre-agents-sdk` pointing at `7ff21dd`.

---

## Phase 1: Server scaffolding

### Task 1.1: Create `server/` package skeleton

**Files:**
- Create: `server/__init__.py`
- Create: `server/config.py`
- Create: `server/requirements.txt`
- Create: `server/tests/__init__.py`
- Create: `server/tests/conftest.py`

- [ ] **Step 1: Write `server/__init__.py`**

```python
"""Nao-OpenAI-Morgan-Assist server package (Python 3.11+)."""
```

- [ ] **Step 2: Write `server/config.py`**

```python
"""Environment-driven configuration for the server."""
import os
from dotenv import load_dotenv

load_dotenv()

# OpenAI
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "gpt-4o-mini")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "gpt-4o-mini")
CHATBOT_MODEL = os.environ.get("CHATBOT_MODEL", "gpt-4o-mini")
THERAPIST_MODEL = os.environ.get("THERAPIST_MODEL", "gpt-4o")
SKILLS_MODEL = os.environ.get("SKILLS_MODEL", "gpt-4o-mini")
CRISIS_MODEL = os.environ.get("CRISIS_MODEL", "gpt-4o-mini")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "whisper-1")

# Pinecone
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
PINECONE_INDEX_NAME = os.environ.get("PINECONE_INDEX_NAME", "msu-cs-knowledge")
PINECONE_NAMESPACE = os.environ.get("PINECONE_NAMESPACE", "docs")

# Networking
NAO_IP = os.environ.get("NAO_IP", "172.20.95.111")
NAO_PORT = int(os.environ.get("NAO_PORT", "9559"))
SERVER_IP = os.environ.get("SERVER_IP", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "5000"))

# Persistence
SESSION_DB = os.environ.get("SESSION_DB", "server/nao.db")

# Tracing (SDK reads OPENAI_AGENTS_DISABLE_TRACING; we keep it on by default)
OPENAI_AGENTS_TRACE = os.environ.get("OPENAI_AGENTS_TRACE", "1") == "1"
```

- [ ] **Step 3: Write `server/requirements.txt`**

```
openai-agents>=0.0.5
openai>=1.50.0
pinecone-client==3.2.2
flask==3.0.3
werkzeug==3.0.3
gunicorn==21.2.0
python-dotenv==1.0.1
requests==2.32.3
pytest==8.3.3
pytest-flask==1.3.0
```

- [ ] **Step 4: Write `server/tests/__init__.py`**

```python
```

- [ ] **Step 5: Write `server/tests/conftest.py`**

```python
import os
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("PINECONE_API_KEY", "pc-test")
os.environ.setdefault("SESSION_DB", ":memory:")
```

- [ ] **Step 6: Install deps in a fresh venv**

```bash
cd server && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```
Expected: all packages install without error.

- [ ] **Step 7: Commit**

```bash
git add server/__init__.py server/config.py server/requirements.txt server/tests/__init__.py server/tests/conftest.py
git commit -m "Scaffold server/ package with config and requirements"
```

---

## Phase 2: Session layer (replaces memory_manager)

### Task 2.1: Write failing test for `get_or_create_session`

**Files:**
- Create: `server/tests/test_session.py`

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_session.py
import pytest
from server import session as s


def test_get_or_create_returns_session_with_username():
    sess = s.get_or_create_session("alice")
    assert sess.session_id == "user:alice"


def test_migrate_username_preserves_history(tmp_path, monkeypatch):
    db = tmp_path / "nao.db"
    monkeypatch.setattr(s, "_DB_PATH", str(db))
    sess = s.get_or_create_session("guest")
    # SQLiteSession exposes add_items per the SDK; use its API
    import asyncio
    asyncio.run(sess.add_items([{"role": "user", "content": "hi"}]))
    s.migrate_username("guest", "alice")
    new_sess = s.get_or_create_session("alice")
    items = asyncio.run(new_sess.get_items())
    assert any("hi" in str(i.get("content", "")) for i in items)


def test_camera_consent_defaults_true_and_persists(tmp_path, monkeypatch):
    db = tmp_path / "nao.db"
    monkeypatch.setattr(s, "_DB_PATH", str(db))
    assert s.get_camera_consent("bob") is True
    s.set_camera_consent("bob", False)
    assert s.get_camera_consent("bob") is False


def test_recap_save_and_load(tmp_path, monkeypatch):
    db = tmp_path / "nao.db"
    monkeypatch.setattr(s, "_DB_PATH", str(db))
    s.save_recap("alice", "Talked about finals stress, practiced reframing catastrophizing.")
    s.save_recap("alice", "Checked in, better mood, discussed advisor meeting.")
    recaps = s.load_recent_recaps("alice", n=3)
    assert len(recaps) == 2
    assert "advisor" in recaps[0]  # newest first
```

- [ ] **Step 2: Run to confirm fail**

```bash
cd server && source .venv/bin/activate && pytest tests/test_session.py -v
```
Expected: ImportError on `from server import session`.

### Task 2.2: Implement `server/session.py`

**Files:**
- Create: `server/session.py`

- [ ] **Step 1: Write `server/session.py`**

```python
"""Session persistence: Agents SDK SQLiteSession + per-user prefs/recaps.

SQLiteSession handles the chat history. We add a tiny side-table for camera
consent and a recaps table for therapist cross-session memory.
"""
from __future__ import annotations

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
    c.execute(
        "CREATE TABLE IF NOT EXISTS recaps ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "username TEXT NOT NULL, body TEXT NOT NULL, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    try:
        yield c
        c.commit()
    finally:
        c.close()


def get_or_create_session(username: str) -> SQLiteSession:
    return SQLiteSession(session_id=f"user:{username}", db_path=_DB_PATH)


def migrate_username(old: str, new: str) -> None:
    """Rename session rows so 'guest' history follows a user after face reco."""
    with _conn() as c:
        c.execute(
            "UPDATE agent_messages SET session_id = ? WHERE session_id = ?",
            (f"user:{new}", f"user:{old}"),
        ) if _table_exists(c, "agent_messages") else None
        c.execute(
            "UPDATE user_prefs SET username = ? WHERE username = ?", (new, old)
        )


def _table_exists(c: sqlite3.Connection, name: str) -> bool:
    row = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def get_camera_consent(username: str) -> bool:
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


def set_camera_consent(username: str, enabled: bool) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO user_prefs (username, camera_consent) VALUES (?, ?) "
            "ON CONFLICT(username) DO UPDATE SET camera_consent=excluded.camera_consent",
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
```

- [ ] **Step 2: Run tests**

```bash
cd server && source .venv/bin/activate && pytest tests/test_session.py -v
```
Expected: all 4 tests pass. If `agent_messages` table naming differs from the SDK, update the migrate test to drive it through the SDK's `add_items`/`get_items` API rather than raw SQL.

- [ ] **Step 3: Commit**

```bash
git add server/session.py server/tests/test_session.py
git commit -m "Add SQLiteSession wrapper with camera consent + recaps"
```

---

## Phase 3: Safety gate (pre-dispatch crisis check)

### Task 3.1: Write failing tests for `crisis_check`

**Files:**
- Create: `server/tests/test_safety.py`

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_safety.py
from unittest.mock import patch, MagicMock

from server import safety


def test_keyword_match_returns_positive_without_llm():
    result = safety.crisis_check("i want to kill myself")
    assert result.positive is True
    assert result.source == "keyword"


def test_benign_phrase_is_negative():
    with patch("server.safety._llm_classify", return_value=False):
        result = safety.crisis_check("i'm really stressed about finals")
        assert result.positive is False


def test_ambiguous_phrase_uses_llm():
    with patch("server.safety._llm_classify", return_value=True) as m:
        result = safety.crisis_check("i don't want to be here anymore")
        assert result.positive is True
        assert result.source == "llm"
        assert m.called


def test_llm_unavailable_falls_back_to_keyword_only_failsafe():
    with patch("server.safety._llm_classify", side_effect=RuntimeError("api down")):
        # ambiguous but no keyword match — fail-safe should flag on any trigger word
        result = safety.crisis_check("i'm done with everything")
        assert result.positive is True
        assert result.source == "failsafe"
```

- [ ] **Step 2: Run to confirm fail**

```bash
cd server && source .venv/bin/activate && pytest tests/test_safety.py -v
```
Expected: ModuleNotFoundError for `server.safety`.

### Task 3.2: Implement `server/safety.py`

**Files:**
- Create: `server/safety.py`

- [ ] **Step 1: Write `server/safety.py`**

```python
"""Pre-dispatch crisis gate. Runs before any agent sees the user message."""
from __future__ import annotations

from dataclasses import dataclass
from openai import OpenAI

from server import config

_CLIENT = OpenAI(api_key=config.OPENAI_API_KEY)

# Hard-fail keywords — any match returns positive immediately, skip LLM.
_HARD_KEYWORDS = (
    "kill myself", "end my life", "suicide", "suicidal",
    "want to die", "going to die tonight",
    "hurt myself", "cutting myself",
)

# Soft triggers — require LLM confirmation. These often appear in benign contexts.
_SOFT_TRIGGERS = (
    "don't want to be here", "can't go on", "no point", "tired of living",
    "done with everything", "give up", "hopeless",
)

HOTLINE_REPLY = (
    "I hear you, and I'm really glad you're telling me. What you're feeling sounds "
    "heavy. I want you to reach out to someone who can be with you right now — you "
    "can call or text 988 in the US for the Suicide and Crisis Lifeline. They're "
    "open 24/7. Is there someone nearby you can talk to too?"
)


@dataclass(frozen=True)
class CrisisResult:
    positive: bool
    source: str  # "keyword" | "llm" | "failsafe" | "clean"


def crisis_check(text: str) -> CrisisResult:
    lower = text.lower()
    if any(k in lower for k in _HARD_KEYWORDS):
        return CrisisResult(True, "keyword")
    soft_hit = any(t in lower for t in _SOFT_TRIGGERS)
    if not soft_hit:
        return CrisisResult(False, "clean")
    try:
        if _llm_classify(text):
            return CrisisResult(True, "llm")
        return CrisisResult(False, "clean")
    except Exception:
        # Fail-safe: if we saw a soft trigger and the LLM is down, flag it.
        return CrisisResult(True, "failsafe")


def _llm_classify(text: str) -> bool:
    resp = _CLIENT.chat.completions.create(
        model=config.CRISIS_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a suicide/self-harm risk classifier. Reply with only "
                    "the word YES or NO. YES if the user is expressing active "
                    "suicidal ideation, intent to self-harm, or immediate crisis. "
                    "NO for general stress, sadness, venting, or hypothetical talk."
                ),
            },
            {"role": "user", "content": text},
        ],
        max_tokens=4,
        temperature=0,
    )
    return resp.choices[0].message.content.strip().upper().startswith("Y")
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/test_safety.py -v
```
Expected: all 4 pass.

- [ ] **Step 3: Commit**

```bash
git add server/safety.py server/tests/test_safety.py
git commit -m "Add pre-dispatch crisis gate with keyword+LLM classifier"
```

---

## Phase 4: NAO action tools + context queue

### Task 4.1: Write failing test for action-queue pattern

**Files:**
- Create: `server/tests/test_nao_actions.py`

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_nao_actions.py
from server.tools import nao_actions


def test_wave_hand_enqueues():
    ctx = {"actions_queue": []}
    result = nao_actions.wave_hand(ctx, hand="right", speed=0.6)
    assert result == "queued"
    assert ctx["actions_queue"] == [
        {"name": "wave_hand", "args": {"hand": "right", "speed": 0.6}}
    ]


def test_multiple_actions_preserve_order():
    ctx = {"actions_queue": []}
    nao_actions.change_eye_color(ctx, color="blue")
    nao_actions.nod_head(ctx, times=2)
    assert [a["name"] for a in ctx["actions_queue"]] == ["change_eye_color", "nod_head"]


def test_all_18_tools_exported():
    expected = {
        "stand_up", "sit_down", "kneel",
        "wave_hand", "wave_both_hands", "nod_head", "shake_head", "clap_hands",
        "move_forward", "move_backward", "turn_left", "turn_right", "spin",
        "dance", "change_eye_color", "follow_movement",
        "set_led_color",  # therapist-friendly alias, distinct tool
        # One extra reserved — document total
    }
    assert expected.issubset(set(nao_actions.ALL_TOOL_NAMES))
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_nao_actions.py -v
```
Expected: ModuleNotFoundError.

### Task 4.2: Implement `server/tools/nao_actions.py`

**Files:**
- Create: `server/tools/__init__.py`
- Create: `server/tools/nao_actions.py`

- [ ] **Step 1: Write `server/tools/__init__.py`**

```python
```

- [ ] **Step 2: Write `server/tools/nao_actions.py`**

```python
"""NAO action tools.

These are declared as regular Agents-SDK function tools, but their *implementation*
doesn't touch NAO. Instead they append a structured {name, args} record to the
shared `actions_queue` in the run context. After `Runner.run()` completes, the
caller reads the queue and returns it in the `/turn` response; NAO executes them
in order.

Keeping execution off the server lets the agent reason naturally ("I'll wave and
turn blue while saying hi") without us needing an RPC back to the robot mid-turn.
"""
from __future__ import annotations

from typing import Any

from agents import RunContextWrapper, function_tool


def _enqueue(ctx, name: str, args: dict[str, Any]) -> str:
    # ctx may be a dict (in unit tests) or a RunContextWrapper (at runtime).
    if isinstance(ctx, RunContextWrapper):
        store = ctx.context
    else:
        store = ctx
    store.setdefault("actions_queue", []).append({"name": name, "args": args})
    return "queued"


# ───────── Posture ─────────

@function_tool
def stand_up(ctx: RunContextWrapper) -> str:
    """Have NAO stand up from sitting or crouching."""
    return _enqueue(ctx, "stand_up", {})


@function_tool
def sit_down(ctx: RunContextWrapper) -> str:
    """Have NAO sit down from standing."""
    return _enqueue(ctx, "sit_down", {})


@function_tool
def kneel(ctx: RunContextWrapper) -> str:
    """Have NAO kneel on one knee."""
    return _enqueue(ctx, "kneel", {})


# ───────── Gesture ─────────

@function_tool
def wave_hand(ctx: RunContextWrapper, hand: str = "right", speed: float = 0.6) -> str:
    """Wave one hand. `hand` is 'left' or 'right'; `speed` is 0.1-1.0."""
    return _enqueue(ctx, "wave_hand", {"hand": hand, "speed": speed})


@function_tool
def wave_both_hands(ctx: RunContextWrapper) -> str:
    """Wave both hands."""
    return _enqueue(ctx, "wave_both_hands", {})


@function_tool
def nod_head(ctx: RunContextWrapper, times: int = 2) -> str:
    """Nod yes 1-5 times."""
    return _enqueue(ctx, "nod_head", {"times": max(1, min(5, times))})


@function_tool
def shake_head(ctx: RunContextWrapper, times: int = 2) -> str:
    """Shake no 1-5 times."""
    return _enqueue(ctx, "shake_head", {"times": max(1, min(5, times))})


@function_tool
def clap_hands(ctx: RunContextWrapper, times: int = 2) -> str:
    """Clap 1-5 times."""
    return _enqueue(ctx, "clap_hands", {"times": max(1, min(5, times))})


# ───────── Movement ─────────

@function_tool
def move_forward(ctx: RunContextWrapper, meters: float = 0.3) -> str:
    """Walk forward `meters` meters."""
    return _enqueue(ctx, "move_forward", {"meters": max(0.0, meters)})


@function_tool
def move_backward(ctx: RunContextWrapper, meters: float = 0.3) -> str:
    """Walk backward `meters` meters."""
    return _enqueue(ctx, "move_backward", {"meters": max(0.0, meters)})


@function_tool
def turn_left(ctx: RunContextWrapper, degrees: float = 45.0) -> str:
    """Turn left in place."""
    return _enqueue(ctx, "turn_left", {"degrees": max(0.0, degrees)})


@function_tool
def turn_right(ctx: RunContextWrapper, degrees: float = 45.0) -> str:
    """Turn right in place."""
    return _enqueue(ctx, "turn_right", {"degrees": max(0.0, degrees)})


@function_tool
def spin(ctx: RunContextWrapper, degrees: float = 360.0) -> str:
    """Spin in place."""
    return _enqueue(ctx, "spin", {"degrees": max(0.0, degrees)})


# ───────── Expression ─────────

@function_tool
def dance(ctx: RunContextWrapper, style: str = "robot") -> str:
    """Run a dance behavior. `style`: 'robot', 'hiphop', or 'salsa'."""
    return _enqueue(ctx, "dance", {"style": style})


@function_tool
def change_eye_color(ctx: RunContextWrapper, color: str = "white") -> str:
    """Set eye LED color. Options: red, green, blue, yellow, purple, white."""
    return _enqueue(ctx, "change_eye_color", {"color": color})


@function_tool
def set_led_color(ctx: RunContextWrapper, color: str = "white") -> str:
    """Alias for change_eye_color used by the therapist agent for mood cues."""
    return _enqueue(ctx, "change_eye_color", {"color": color})


@function_tool
def follow_movement(ctx: RunContextWrapper) -> str:
    """NAO mirrors the user's upper-body motions."""
    return _enqueue(ctx, "follow_movement", {})


# ───────── Bundles ─────────

CHAT_ACTIONS = [
    stand_up, sit_down, kneel,
    wave_hand, wave_both_hands, nod_head, shake_head, clap_hands,
    move_forward, move_backward, turn_left, turn_right, spin,
    dance, change_eye_color, follow_movement,
]

THERAPIST_ACTIONS = [
    set_led_color, nod_head,
]

ALL_TOOL_NAMES = {t.name for t in CHAT_ACTIONS} | {"set_led_color"}
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/test_nao_actions.py -v
```
Expected: 3/3 pass.

- [ ] **Step 4: Commit**

```bash
git add server/tools/__init__.py server/tools/nao_actions.py server/tests/test_nao_actions.py
git commit -m "Add NAO action tools with context-scoped actions_queue"
```

---

## Phase 5: Data tools

### Task 5.1: Pinecone search tool

**Files:**
- Create: `server/tools/pinecone_search.py`
- Create: `server/tests/test_pinecone_search.py`

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_pinecone_search.py
from unittest.mock import patch, MagicMock

from server.tools import pinecone_search


def test_search_returns_top_k_texts():
    fake_match = MagicMock(metadata={"text": "CS 341 covers data structures."}, score=0.9)
    fake_index = MagicMock()
    fake_index.query.return_value = MagicMock(matches=[fake_match])
    with patch.object(pinecone_search, "_index", fake_index), \
         patch.object(pinecone_search, "_embed", return_value=[0.1] * 1536):
        results = pinecone_search._search_impl("what is cs 341")
    assert "CS 341" in results[0]["text"]
    assert results[0]["score"] == 0.9
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_pinecone_search.py -v
```
Expected: ImportError.

- [ ] **Step 3: Write `server/tools/pinecone_search.py`**

```python
"""RAG tool: embed → Pinecone top-k → structured results."""
from __future__ import annotations

from pinecone import Pinecone
from openai import OpenAI

from agents import function_tool
from server import config

_pc = Pinecone(api_key=config.PINECONE_API_KEY) if config.PINECONE_API_KEY else None
_index = _pc.Index(config.PINECONE_INDEX_NAME) if _pc else None
_client = OpenAI(api_key=config.OPENAI_API_KEY)


def _embed(text: str) -> list[float]:
    r = _client.embeddings.create(model="text-embedding-3-small", input=text)
    return r.data[0].embedding


def _search_impl(query: str, top_k: int = 5) -> list[dict]:
    if _index is None:
        return []
    emb = _embed(query)
    res = _index.query(
        vector=emb, top_k=top_k, namespace=config.PINECONE_NAMESPACE,
        include_metadata=True,
    )
    return [
        {"text": m.metadata.get("text", ""), "score": float(m.score)}
        for m in res.matches
    ]


@function_tool
def pinecone_search(query: str, top_k: int = 5) -> list[dict]:
    """Search the Morgan State CS knowledge base. Returns top_k passages with scores."""
    return _search_impl(query, top_k)
```

- [ ] **Step 4: Run test, commit**

```bash
pytest tests/test_pinecone_search.py -v
git add server/tools/pinecone_search.py server/tests/test_pinecone_search.py
git commit -m "Add pinecone_search RAG tool"
```

### Task 5.2: Emotion tools

**Files:**
- Create: `server/tools/emotion.py`
- Create: `server/tests/test_emotion.py`

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_emotion.py
from unittest.mock import patch

from server.tools import emotion


def test_log_emotion_appends_to_context():
    ctx = {"emotion_log": []}
    result = emotion._log_emotion_impl(ctx, mood="sad", intensity=7, trigger="exam stress")
    assert result == "logged"
    assert ctx["emotion_log"][0]["mood"] == "sad"


def test_identify_distortion_returns_known_label():
    with patch("server.tools.emotion._classify_distortion",
               return_value={"distortion": "catastrophizing", "explanation": "..."}):
        out = emotion._identify_distortion_impl("everything is ruined forever")
        assert out["distortion"] == "catastrophizing"


def test_observe_face_with_no_image_returns_error():
    ctx = {"latest_image_b64": None}
    out = emotion._observe_face_impl(ctx)
    assert out == {"error": "no_image"}


def test_observe_face_with_image_returns_emotions(monkeypatch):
    ctx = {"latest_image_b64": "fakebytes"}
    monkeypatch.setattr(
        emotion, "_vision_classify",
        lambda b64: {"dominant_emotion": "sad", "secondary": "tired", "notes": "..."},
    )
    out = emotion._observe_face_impl(ctx)
    assert out["dominant_emotion"] == "sad"
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_emotion.py -v
```
Expected: ImportError.

- [ ] **Step 3: Write `server/tools/emotion.py`**

```python
"""Emotion tools for the therapist + CBT + grounding agents."""
from __future__ import annotations

import base64 as _b64
import json
from typing import Any

from openai import OpenAI

from agents import RunContextWrapper, function_tool
from server import config, session

_client = OpenAI(api_key=config.OPENAI_API_KEY)

_DISTORTIONS = (
    "catastrophizing", "all-or-nothing", "mind reading", "personalization",
    "fortune-telling", "emotional reasoning", "shoulds", "labeling",
    "magnification/minimization", "filtering",
)


def _unwrap(ctx) -> dict:
    return ctx.context if isinstance(ctx, RunContextWrapper) else ctx


# ────────── log_emotion ──────────

def _log_emotion_impl(ctx, mood: str, intensity: int, trigger: str) -> str:
    store = _unwrap(ctx)
    store.setdefault("emotion_log", []).append(
        {"mood": mood, "intensity": intensity, "trigger": trigger}
    )
    return "logged"


@function_tool
def log_emotion(ctx: RunContextWrapper, mood: str, intensity: int, trigger: str) -> str:
    """Log a per-turn emotion read (mood, intensity 1-10, trigger) for session recap."""
    return _log_emotion_impl(ctx, mood, intensity, trigger)


# ────────── identify_distortion / suggest_reframe ──────────

def _classify_distortion(thought: str) -> dict:
    prompt = (
        "Classify the cognitive distortion in the user's thought. Choose exactly "
        "ONE from: " + ", ".join(_DISTORTIONS) + ". Respond as JSON: "
        '{"distortion": "<name>", "explanation": "<one sentence, gentle tone>"}'
    )
    resp = _client.chat.completions.create(
        model=config.CRISIS_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": thought},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    return json.loads(resp.choices[0].message.content)


def _identify_distortion_impl(thought: str) -> dict:
    return _classify_distortion(thought)


@function_tool
def identify_distortion(thought: str) -> dict:
    """Identify one CBT cognitive distortion in the user's thought with a gentle explanation."""
    return _identify_distortion_impl(thought)


def _reframe_impl(thought: str, distortion: str) -> list[str]:
    prompt = (
        f"The user has a thought exhibiting {distortion}. Offer 2 balanced, "
        "compassionate alternative thoughts they could consider. Reply as a JSON "
        'list of 2 strings: {"reframes": ["...", "..."]}'
    )
    resp = _client.chat.completions.create(
        model=config.CRISIS_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": thought},
        ],
        response_format={"type": "json_object"},
        temperature=0.4,
    )
    return json.loads(resp.choices[0].message.content)["reframes"]


@function_tool
def suggest_reframe(thought: str, distortion: str) -> list[str]:
    """Return two balanced reframes for a thought exhibiting the given distortion."""
    return _reframe_impl(thought, distortion)


# ────────── observe_face ──────────

def _vision_classify(image_b64: str) -> dict:
    data_uri = f"data:image/jpeg;base64,{image_b64}"
    resp = _client.chat.completions.create(
        model=config.THERAPIST_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You read facial expressions for a supportive robot companion. "
                    "Return JSON: "
                    '{"dominant_emotion": "...", "secondary": "...", "notes": "..."} '
                    "where emotions are one of: happy, sad, angry, fearful, "
                    "surprised, disgusted, neutral, tired, stressed."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What do you see?"},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    return json.loads(resp.choices[0].message.content)


def _observe_face_impl(ctx) -> dict:
    store = _unwrap(ctx)
    b64 = store.get("latest_image_b64")
    if not b64:
        return {"error": "no_image"}
    return _vision_classify(b64)


@function_tool
def observe_face(ctx: RunContextWrapper) -> dict:
    """Read the user's face from the current turn's image. Returns {error:'no_image'} if none attached."""
    return _observe_face_impl(ctx)


# ────────── camera consent ──────────

def _set_camera_consent_impl(ctx, enabled: bool) -> str:
    store = _unwrap(ctx)
    username = store.get("username", "guest")
    session.set_camera_consent(username, enabled)
    if not enabled:
        store["suppress_image"] = True
    else:
        store["suppress_image"] = False
    return f"camera_consent={enabled}"


@function_tool
def set_camera_consent(ctx: RunContextWrapper, enabled: bool) -> str:
    """Set the user's camera consent. When False, NAO stops uploading images this session and next visits."""
    return _set_camera_consent_impl(ctx, enabled)


# ────────── recap_session ──────────

def _recap_session_impl(ctx) -> str:
    store = _unwrap(ctx)
    username = store.get("username", "guest")
    log = store.get("emotion_log", [])
    if not log:
        body = "Brief check-in; no notable thoughts logged."
    else:
        moods = ", ".join(f"{e['mood']}({e['intensity']})" for e in log[-5:])
        body = f"Emotions: {moods}. Triggers: {'; '.join(e['trigger'] for e in log[-5:])}."
    session.save_recap(username, body)
    return body


@function_tool
def recap_session(ctx: RunContextWrapper) -> str:
    """Summarize this therapy session and persist it to the user's history."""
    return _recap_session_impl(ctx)
```

- [ ] **Step 4: Run tests, commit**

```bash
pytest tests/test_emotion.py -v
git add server/tools/emotion.py server/tests/test_emotion.py
git commit -m "Add therapist emotion/CBT/vision tools"
```

### Task 5.3: Skills tools (replaces mini_nao)

**Files:**
- Create: `server/tools/skills_tools.py`
- Create: `server/tests/test_skills_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# server/tests/test_skills_tools.py
from unittest.mock import patch, MagicMock

from server.tools import skills_tools


def test_get_time_returns_ny_tz():
    t = skills_tools._get_time_impl()
    assert "America/New_York" in t["timezone"]
    assert ":" in t["time"]


def test_get_weather_baltimore_shape():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {
        "current": {"temperature_2m": 55.0, "weather_code": 3, "relative_humidity_2m": 60}
    }
    with patch("server.tools.skills_tools.requests.get", return_value=fake_resp):
        w = skills_tools._get_weather_impl()
    assert w["temperature_f"] == 55.0
    assert "condition" in w


def test_todo_add_list_complete_cycle():
    store = {"todos": []}
    skills_tools._add_todo_impl(store, "write spec")
    skills_tools._add_todo_impl(store, "ship code")
    assert len(skills_tools._list_todos_impl(store)) == 2
    skills_tools._complete_todo_impl(store, 1)
    remaining = skills_tools._list_todos_impl(store)
    assert len(remaining) == 1
    assert remaining[0]["text"] == "ship code"
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_skills_tools.py -v
```
Expected: ImportError.

- [ ] **Step 3: Write `server/tools/skills_tools.py`**

```python
"""Time, weather, timers, reminders, todos. Replaces the old mini_nao."""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from agents import RunContextWrapper, function_tool

_NY = ZoneInfo("America/New_York")
_BALT_LAT, _BALT_LON = 39.2904, -76.6122


def _unwrap(ctx) -> dict:
    from agents import RunContextWrapper as W  # local alias to avoid circular
    return ctx.context if isinstance(ctx, W) else ctx


# ────── time / date ──────

def _get_time_impl() -> dict:
    now = datetime.now(_NY)
    return {"time": now.strftime("%-I:%M %p"), "timezone": "America/New_York"}


@function_tool
def get_time() -> dict:
    """Current time in New York (Eastern time)."""
    return _get_time_impl()


@function_tool
def get_date() -> dict:
    """Today's date."""
    now = datetime.now(_NY)
    return {"date": now.strftime("%A, %B %-d, %Y")}


# ────── weather ──────

_WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow",
    80: "rain showers", 95: "thunderstorm",
}


def _get_weather_impl() -> dict:
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": _BALT_LAT, "longitude": _BALT_LON,
            "current": "temperature_2m,weather_code,relative_humidity_2m",
            "temperature_unit": "fahrenheit",
        },
        timeout=5,
    )
    data = r.json()["current"]
    return {
        "temperature_f": data["temperature_2m"],
        "condition": _WEATHER_CODES.get(data["weather_code"], "unknown"),
        "humidity": data["relative_humidity_2m"],
    }


@function_tool
def get_weather_baltimore() -> dict:
    """Current weather for Baltimore via Open-Meteo (no API key needed)."""
    return _get_weather_impl()


# ────── timers / reminders ──────

def _set_timer_impl(store: dict, seconds: int, label: str = "timer") -> dict:
    fire = int(time.time()) + max(1, seconds)
    tid = len(store.setdefault("timers", [])) + 1
    entry = {"id": tid, "fire_at": fire, "label": label}
    store["timers"].append(entry)
    return entry


@function_tool
def set_timer(ctx: RunContextWrapper, seconds: int, label: str = "timer") -> dict:
    """Set a timer that fires in `seconds` seconds. Label helps the user recognize it."""
    return _set_timer_impl(_unwrap(ctx), seconds, label)


# ────── todos ──────

def _add_todo_impl(store: dict, text: str) -> dict:
    items = store.setdefault("todos", [])
    tid = len(items) + 1
    entry = {"id": tid, "text": text, "done": False}
    items.append(entry)
    return entry


@function_tool
def add_todo(ctx: RunContextWrapper, text: str) -> dict:
    """Add a todo."""
    return _add_todo_impl(_unwrap(ctx), text)


def _list_todos_impl(store: dict) -> list[dict]:
    return [t for t in store.get("todos", []) if not t["done"]]


@function_tool
def list_todos(ctx: RunContextWrapper) -> list[dict]:
    """List open todos."""
    return _list_todos_impl(_unwrap(ctx))


def _complete_todo_impl(store: dict, todo_id: int) -> str:
    for t in store.get("todos", []):
        if t["id"] == todo_id:
            t["done"] = True
            return "done"
    return "not_found"


@function_tool
def complete_todo(ctx: RunContextWrapper, todo_id: int) -> str:
    """Mark a todo complete by id."""
    return _complete_todo_impl(_unwrap(ctx), todo_id)
```

- [ ] **Step 4: Run tests, commit**

```bash
pytest tests/test_skills_tools.py -v
git add server/tools/skills_tools.py server/tests/test_skills_tools.py
git commit -m "Add skills tools (time, weather, timers, todos)"
```

---

## Phase 6: Agents

### Task 6.1: Build the agent graph

**Files:**
- Create: `server/agents/__init__.py`
- Create: `server/agents/chat.py`
- Create: `server/agents/chatbot.py`
- Create: `server/agents/skills.py`
- Create: `server/agents/cbt_coach.py`
- Create: `server/agents/grounding_coach.py`
- Create: `server/agents/therapist.py`
- Create: `server/agents/router.py`
- Create: `server/tests/test_agents.py`

- [ ] **Step 1: Write `server/agents/chat.py`**

```python
"""Chat specialist — open conversation with NAO action tools."""
from agents import Agent
from server import config
from server.tools.nao_actions import CHAT_ACTIONS

SYSTEM = (
    "You are a friendly NAO humanoid robot chatting with a student. Keep replies "
    "under 2 short sentences. When the user asks for physical actions (wave, dance, "
    "nod, change eye color, etc.), call the matching tool. You can call multiple "
    "action tools in one turn."
)

chat_agent = Agent(
    name="chat",
    instructions=SYSTEM,
    model=config.CHAT_MODEL,
    tools=CHAT_ACTIONS,
)
```

- [ ] **Step 2: Write `server/agents/chatbot.py`**

```python
"""Chatbot specialist — Morgan State CS knowledge base RAG."""
from agents import Agent
from server import config
from server.tools.pinecone_search import pinecone_search
from server.tools.nao_actions import nod_head, change_eye_color

SYSTEM = (
    "You are a Morgan State University Computer Science department assistant on a "
    "NAO robot. For any factual question about the CS department, courses, faculty, "
    "or programs, call `pinecone_search` first and ground your answer in the "
    "returned passages. Keep replies under 3 sentences. Say 'I'm not sure' if "
    "search returns nothing useful."
)

chatbot_agent = Agent(
    name="chatbot",
    instructions=SYSTEM,
    model=config.CHATBOT_MODEL,
    tools=[pinecone_search, nod_head, change_eye_color],
)
```

- [ ] **Step 3: Write `server/agents/skills.py`**

```python
"""Skills specialist — time, weather, timers, todos."""
from agents import Agent
from server import config
from server.tools.skills_tools import (
    get_time, get_date, get_weather_baltimore,
    set_timer, add_todo, list_todos, complete_todo,
)

SYSTEM = (
    "You are NAO's utility assistant. Handle time, date, weather, timers, and todos "
    "by calling the matching tool, then reply with the result in one short sentence."
)

skills_agent = Agent(
    name="skills",
    instructions=SYSTEM,
    model=config.SKILLS_MODEL,
    tools=[get_time, get_date, get_weather_baltimore,
           set_timer, add_todo, list_todos, complete_todo],
)
```

- [ ] **Step 4: Write `server/agents/cbt_coach.py`**

```python
"""CBT coach — walks a thought record when the therapist hands off."""
from agents import Agent
from server import config
from server.tools.emotion import identify_distortion, suggest_reframe, log_emotion

SYSTEM = (
    "You are a CBT (Cognitive Behavioral Therapy) coach on a NAO robot. You are "
    "not a therapist and do not diagnose. Walk the user through ONE thought "
    "record, one step at a time, asking only one question per turn:\n"
    "1) What happened?\n"
    "2) What thought went through your mind?\n"
    "3) How did that make you feel, 1-10?\n"
    "4) What's the evidence FOR the thought? Evidence AGAINST?\n"
    "5) What's a more balanced way to see it?\n\n"
    "Use `identify_distortion` after step 2 to name the distortion gently. Use "
    "`suggest_reframe` during step 5 to offer 2 balanced alternatives. When the "
    "user has a reframe they like, hand back to the therapist. Keep every reply "
    "under 2 short sentences. Never rush the user."
)

cbt_coach_agent = Agent(
    name="cbt_coach",
    instructions=SYSTEM,
    model=config.THERAPIST_MODEL,
    tools=[identify_distortion, suggest_reframe, log_emotion],
)
```

- [ ] **Step 5: Write `server/agents/grounding_coach.py`**

```python
"""Grounding coach — runs one grounding exercise on therapist handoff."""
from agents import Agent
from server import config
from server.tools.emotion import observe_face

SYSTEM = (
    "You are a grounding coach on a NAO robot. Pick ONE exercise based on the "
    "user's state and walk them through it, one step per turn:\n"
    "• 5-4-3-2-1 senses (for dissociation/anxiety): name 5 things you see, "
    "4 things you hear, 3 things you feel, 2 things you smell, 1 thing you taste.\n"
    "• Box breathing (for panic): 4s in, 4s hold, 4s out, 4s hold, 3 rounds.\n"
    "• Body scan (for tension): head to toe, 5 regions.\n\n"
    "You can call `observe_face` at any point to check how the user is doing. "
    "When the exercise is done, ask how they feel and hand back to the therapist."
)

grounding_coach_agent = Agent(
    name="grounding_coach",
    instructions=SYSTEM,
    model=config.THERAPIST_MODEL,
    tools=[observe_face],
)
```

- [ ] **Step 6: Write `server/agents/therapist.py`**

```python
"""Therapist main agent — empathetic, CBT/grounding handoffs, camera consent."""
from agents import Agent, handoff
from server import config, session
from server.tools.nao_actions import THERAPIST_ACTIONS
from server.tools.emotion import (
    observe_face, log_emotion, identify_distortion, suggest_reframe,
    set_camera_consent, recap_session,
)
from server.agents.cbt_coach import cbt_coach_agent
from server.agents.grounding_coach import grounding_coach_agent

_BASE = (
    "You are a warm, non-clinical companion on a NAO robot for Morgan State "
    "students. You are NOT a therapist and you NEVER diagnose. Your priorities, "
    "in order:\n"
    "1) Listen and validate first. 'I hear you' before any technique.\n"
    "2) Use `observe_face` when helpful to check facial emotion.\n"
    "3) Call `log_emotion` every turn to track mood + trigger.\n"
    "4) If the user dwells on a single distorted thought → hand off to cbt_coach.\n"
    "5) If the user is panicking or overwhelmed → hand off to grounding_coach.\n"
    "6) On first turn of a session, ask for camera consent (see below). Call "
    "   `set_camera_consent(true)` or `set_camera_consent(false)` based on reply.\n"
    "7) For anything serious or ongoing, gently recommend a professional.\n\n"
    "Tone: warm, curious, under 2 sentences per reply. No unsolicited advice.\n"
    "Camera consent line: \"I can use my camera to get a better read of how "
    "you're feeling — is that okay? Say 'no camera' if you'd rather I didn't.\"\n"
)


def build_therapist_agent(username: str) -> Agent:
    recaps = session.load_recent_recaps(username, n=3)
    recap_block = (
        "\n\nRecent sessions:\n" + "\n".join(f"- {r}" for r in recaps)
        if recaps else ""
    )
    return Agent(
        name="therapist",
        instructions=_BASE + recap_block,
        model=config.THERAPIST_MODEL,
        tools=[observe_face, log_emotion, identify_distortion, suggest_reframe,
               set_camera_consent, recap_session, *THERAPIST_ACTIONS],
        handoffs=[
            handoff(cbt_coach_agent),
            handoff(grounding_coach_agent),
        ],
    )
```

- [ ] **Step 7: Write `server/agents/router.py`**

```python
"""Router — triage agent that picks a specialist."""
from agents import Agent, handoff
from server import config
from server.agents.chat import chat_agent
from server.agents.chatbot import chatbot_agent
from server.agents.skills import skills_agent
from server.agents.therapist import build_therapist_agent

SYSTEM = (
    "You are the triage agent for a NAO robot assistant. Read the user's first "
    "message and hand off to exactly one specialist:\n"
    "• chatbot — Morgan State CS department questions (courses, faculty, programs)\n"
    "• skills — time, date, weather, timers, todos\n"
    "• therapist — emotional topics, stress, relationships, feelings\n"
    "• chat — everything else (open conversation, physical actions)\n\n"
    "Do not answer yourself. Always hand off."
)


def build_router(username: str) -> Agent:
    return Agent(
        name="router",
        instructions=SYSTEM,
        model=config.ROUTER_MODEL,
        handoffs=[
            handoff(chat_agent),
            handoff(chatbot_agent),
            handoff(skills_agent),
            handoff(build_therapist_agent(username)),
        ],
    )
```

- [ ] **Step 8: Write `server/agents/__init__.py`**

```python
"""Agent graph builders."""
from server.agents.chat import chat_agent
from server.agents.chatbot import chatbot_agent
from server.agents.skills import skills_agent
from server.agents.therapist import build_therapist_agent
from server.agents.router import build_router


def pick_initial_agent(username: str, hint: str | None):
    """Return the agent to start a turn with, based on optional wake-phrase hint."""
    if hint == "chat":
        return chat_agent
    if hint == "morgan":
        return chatbot_agent
    if hint == "therapy":
        return build_therapist_agent(username)
    if hint == "skills":
        return skills_agent
    return build_router(username)
```

- [ ] **Step 9: Write `server/tests/test_agents.py`**

```python
from server.agents import pick_initial_agent


def test_hint_chat_picks_chat_agent():
    assert pick_initial_agent("alice", "chat").name == "chat"


def test_hint_morgan_picks_chatbot():
    assert pick_initial_agent("alice", "morgan").name == "chatbot"


def test_hint_therapy_picks_therapist():
    assert pick_initial_agent("alice", "therapy").name == "therapist"


def test_hint_skills_picks_skills():
    assert pick_initial_agent("alice", "skills").name == "skills"


def test_no_hint_returns_router():
    assert pick_initial_agent("alice", None).name == "router"


def test_therapist_injects_recaps(monkeypatch):
    from server.agents import therapist as t
    monkeypatch.setattr(t.session, "load_recent_recaps", lambda u, n=3: ["past talk"])
    a = t.build_therapist_agent("alice")
    assert "past talk" in a.instructions
```

- [ ] **Step 10: Run tests, commit**

```bash
pytest tests/test_agents.py -v
git add server/agents/ server/tests/test_agents.py
git commit -m "Add agent graph: router + chat + chatbot + skills + therapist + cbt + grounding"
```

---

## Phase 7: Flask /turn endpoint

### Task 7.1: Write failing integration test for /turn

**Files:**
- Create: `server/tests/fixtures/sample.wav` (small synthesized WAV; 0.5s sine at 440Hz)
- Create: `server/tests/test_turn_endpoint.py`

- [ ] **Step 1: Generate the test WAV**

```bash
cd server/tests && mkdir -p fixtures
python3 -c "
import wave, struct, math
w = wave.open('fixtures/sample.wav', 'wb')
w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
for i in range(8000):
    w.writeframes(struct.pack('<h', int(16000 * math.sin(2*math.pi*440*i/16000))))
w.close()
"
ls -la fixtures/sample.wav
```

- [ ] **Step 2: Write failing test**

```python
# server/tests/test_turn_endpoint.py
import io
from unittest.mock import patch, MagicMock

import pytest

from server.server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


def _fake_run_result(reply="hi", actions=None):
    m = MagicMock()
    m.final_output = reply
    # inject actions after the fact via run context — see implementation
    return m


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_turn_happy_path_general(client):
    with open("tests/fixtures/sample.wav", "rb") as f:
        audio_bytes = f.read()
    with patch("server.server._transcribe", return_value="hello"), \
         patch("server.server.crisis_check") as crisis, \
         patch("server.server._run_agent") as runner:
        crisis.return_value = MagicMock(positive=False, source="clean")
        runner.return_value = ("Hello back!", "chat", [
            {"name": "wave_hand", "args": {"hand": "right"}}
        ], False)
        r = client.post("/turn", data={
            "audio": (io.BytesIO(audio_bytes), "sample.wav"),
            "username": "alice",
            "hint": "chat",
        }, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["reply"] == "Hello back!"
    assert body["active_agent"] == "chat"
    assert body["actions"][0]["name"] == "wave_hand"
    assert body["crisis"] is False


def test_turn_crisis_bypasses_agent(client):
    with open("tests/fixtures/sample.wav", "rb") as f:
        audio_bytes = f.read()
    with patch("server.server._transcribe", return_value="i want to kill myself"), \
         patch("server.server._run_agent") as runner:
        r = client.post("/turn", data={
            "audio": (io.BytesIO(audio_bytes), "sample.wav"),
            "username": "bob",
        }, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["crisis"] is True
    assert "988" in body["reply"]
    assert not runner.called


def test_turn_end_session_triggers_recap(client):
    with patch("server.server._run_recap") as recap:
        recap.return_value = "recap saved"
        r = client.post("/turn", data={
            "username": "alice",
            "end_session": "true",
        }, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("reply") == "recap saved"
    assert recap.called
```

- [ ] **Step 3: Run to confirm fail**

```bash
pytest tests/test_turn_endpoint.py -v
```
Expected: ModuleNotFoundError `server.server`.

### Task 7.2: Implement `server/server.py`

**Files:**
- Create: `server/server.py`

- [ ] **Step 1: Write `server/server.py`**

```python
"""Flask app exposing POST /turn for NAO."""
from __future__ import annotations

import asyncio
import base64
import os
import tempfile
import wave

from flask import Flask, jsonify, request
from openai import OpenAI

from agents import Runner

from server import config, safety, session
from server.agents import pick_initial_agent

app = Flask(__name__)
_client = OpenAI(api_key=config.OPENAI_API_KEY)


# ───────── helpers ─────────

def _validate_wav(path: str) -> bool:
    if os.path.getsize(path) < 400:
        return False
    try:
        with wave.open(path, "rb") as w:
            dur = w.getnframes() / float(w.getframerate() or 1)
            return dur >= 0.12
    except Exception:
        return False


def _transcribe(path: str) -> str:
    with open(path, "rb") as f:
        resp = _client.audio.transcriptions.create(
            model=config.WHISPER_MODEL, file=f,
        )
    return resp.text


def _build_user_message(transcript: str, image_b64: str | None):
    if not image_b64:
        return transcript
    return [
        {"type": "text", "text": transcript},
        {"type": "image_url", "image_url": {
            "url": f"data:image/jpeg;base64,{image_b64}",
        }},
    ]


def _run_agent(username: str, hint: str | None, transcript: str,
               image_b64: str | None) -> tuple[str, str, list[dict], bool]:
    agent = pick_initial_agent(username, hint)
    sess = session.get_or_create_session(username)
    ctx = {
        "username": username,
        "actions_queue": [],
        "emotion_log": [],
        "latest_image_b64": image_b64,
        "suppress_image": False,
    }
    message = _build_user_message(transcript, image_b64)
    result = asyncio.run(Runner.run(agent, message, context=ctx, session=sess))
    active = getattr(result, "last_agent", agent).name
    return (
        result.final_output,
        active,
        list(ctx["actions_queue"]),
        bool(ctx["suppress_image"]),
    )


def _run_recap(username: str) -> str:
    """Build the therapist for this user, call its recap_session tool implementation."""
    from server.tools.emotion import _recap_session_impl
    # We don't have a live context with an emotion_log here — load from session history
    # by running a minimal tool invocation. For now, persist a neutral recap.
    ctx = {"username": username, "emotion_log": []}
    return _recap_session_impl(ctx)


# ───────── routes ─────────

@app.get("/health")
def health():
    return jsonify(ok=True)


@app.post("/turn")
def turn():
    username = request.form.get("username") or "guest"
    hint = request.form.get("hint") or None
    end_session = request.form.get("end_session", "").lower() == "true"

    if end_session:
        body = _run_recap(username)
        return jsonify(
            username=username, user_input="", reply=body,
            active_agent="therapist", actions=[], crisis=False,
            suppress_image=False,
        )

    audio = request.files.get("audio")
    image = request.files.get("image")
    if not audio:
        return jsonify(error="missing_audio"), 400

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio.save(tmp.name)
        wav_path = tmp.name
    try:
        if not _validate_wav(wav_path):
            return jsonify(error="invalid_audio"), 503
        transcript = _transcribe(wav_path)
    finally:
        os.unlink(wav_path)

    crisis = safety.crisis_check(transcript)
    if crisis.positive:
        return jsonify(
            username=username, user_input=transcript,
            reply=safety.HOTLINE_REPLY, active_agent="safety",
            actions=[{"name": "change_eye_color", "args": {"color": "white"}}],
            crisis=True, suppress_image=False,
        )

    # Respect persisted camera consent
    consent = session.get_camera_consent(username)
    image_b64 = None
    if image and consent:
        image_b64 = base64.b64encode(image.read()).decode("ascii")

    reply, active, actions, suppress = _run_agent(username, hint, transcript, image_b64)

    return jsonify(
        username=username, user_input=transcript, reply=reply,
        active_agent=active, actions=actions, crisis=False,
        suppress_image=suppress,
    )


if __name__ == "__main__":
    app.run(host=config.SERVER_IP, port=config.SERVER_PORT, debug=False)
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/test_turn_endpoint.py -v
```
Expected: all 3 pass. Fix any import paths.

- [ ] **Step 3: Commit**

```bash
git add server/server.py server/tests/test_turn_endpoint.py server/tests/fixtures/
git commit -m "Add Flask /turn endpoint wiring transcribe+crisis+agent runner"
```

### Task 7.3: Smoke-test the server end-to-end with curl

**Files:** none

- [ ] **Step 1: Start server**

```bash
cd server && source .venv/bin/activate && python -m server.server &
```

- [ ] **Step 2: Hit /health**

```bash
curl -s localhost:5000/health
```
Expected: `{"ok":true}`.

- [ ] **Step 3: Hit /turn with sample WAV and chat hint**

```bash
curl -s -X POST localhost:5000/turn \
  -F "audio=@tests/fixtures/sample.wav" \
  -F "username=smoketest" \
  -F "hint=chat"
```
Expected: JSON with `reply`, `active_agent: "chat"`, empty or populated `actions[]`, `crisis: false`.

- [ ] **Step 4: Kill server**

```bash
kill %1
```

---

## Phase 8: NAO-side consolidation

### Task 8.1: Add `utils/camera_capture.snap_quick`

**Files:**
- Modify: `utils/camera_capture.py`

- [ ] **Step 1: Append `snap_quick`**

Read current `utils/camera_capture.py`. At the bottom, append:

```python
def snap_quick(nao_ip, port=9559, resolution=1, color_space=11, path=None):
    """Capture a quick 640x480 JPEG via ALPhotoCapture. Returns local path or None on failure.

    resolution=1 -> kQVGA (640x480); color_space=11 -> kRGBColorSpace.
    """
    try:
        from naoqi import ALProxy
        import time, os
        photo = ALProxy("ALPhotoCapture", nao_ip, port)
        photo.setResolution(resolution)
        photo.setPictureFormat("jpg")
        out_dir = "/home/nao/snaps"
        try: os.makedirs(out_dir)
        except OSError: pass
        name = "snap_{0}".format(int(time.time() * 1000))
        photo.takePicture(out_dir, name)
        full = os.path.join(out_dir, name + ".jpg")
        return full if os.path.exists(full) else None
    except Exception:
        return None
```

- [ ] **Step 2: Commit**

```bash
git add utils/camera_capture.py
git commit -m "Add camera_capture.snap_quick for per-turn JPEG"
```

### Task 8.2: Add `utils/nao_execute.py`

**Files:**
- Create: `utils/nao_execute.py`

- [ ] **Step 1: Write `utils/nao_execute.py`**

```python
# -*- coding: utf-8 -*-
"""Dispatch {name, args} records from the server to naoqi calls on NAO (Py 2.7)."""
from __future__ import print_function


_EYE_COLORS = {
    "red": 0xFF0000, "green": 0x00FF00, "blue": 0x0000FF,
    "yellow": 0xFFFF00, "purple": 0x800080, "white": 0xFFFFFF,
}


def run(action, session, motion, posture, leds, behav_mgr, tts):
    """Execute a single action dict. Silently no-ops on unknown names."""
    name = action.get("name")
    args = action.get("args") or {}
    try:
        if name == "stand_up":
            posture.goToPosture("StandInit", 0.6)
        elif name == "sit_down":
            posture.goToPosture("Sit", 0.6)
        elif name == "kneel":
            posture.goToPosture("Crouch", 0.6)
        elif name == "wave_hand":
            hand = args.get("hand", "right")
            behav_mgr.runBehavior("animations/Stand/Gestures/Hey_{0}".format(
                "1" if hand == "right" else "3"))
        elif name == "wave_both_hands":
            behav_mgr.runBehavior("animations/Stand/Gestures/Hey_1")
            behav_mgr.runBehavior("animations/Stand/Gestures/Hey_3")
        elif name == "nod_head":
            n = int(args.get("times", 2))
            for _ in range(n):
                motion.angleInterpolation(["HeadPitch"], [0.3, -0.1], [0.5, 1.0], True)
        elif name == "shake_head":
            n = int(args.get("times", 2))
            for _ in range(n):
                motion.angleInterpolation(["HeadYaw"], [0.5, -0.5], [0.4, 0.8], True)
        elif name == "clap_hands":
            n = int(args.get("times", 2))
            for _ in range(n):
                behav_mgr.runBehavior("animations/Stand/Emotions/Positive/Happy_4")
        elif name == "move_forward":
            motion.moveTo(float(args.get("meters", 0.3)), 0.0, 0.0)
        elif name == "move_backward":
            motion.moveTo(-float(args.get("meters", 0.3)), 0.0, 0.0)
        elif name == "turn_left":
            import math
            motion.moveTo(0.0, 0.0, math.radians(float(args.get("degrees", 45.0))))
        elif name == "turn_right":
            import math
            motion.moveTo(0.0, 0.0, -math.radians(float(args.get("degrees", 45.0))))
        elif name == "spin":
            import math
            motion.moveTo(0.0, 0.0, math.radians(float(args.get("degrees", 360.0))))
        elif name == "dance":
            style = args.get("style", "robot")
            behav_mgr.runBehavior("dance-{0}/behavior_1".format(style))
        elif name == "change_eye_color":
            color = _EYE_COLORS.get(args.get("color", "white"), 0xFFFFFF)
            leds.fadeRGB("FaceLeds", color, 0.3)
        elif name == "follow_movement":
            # Enable upper-body mirroring; left as a stub to avoid long blocking.
            pass
        else:
            print("[nao_execute] unknown action:", name)
    except Exception as e:
        print("[nao_execute] action failed:", name, "error:", e)
```

- [ ] **Step 2: Commit**

```bash
git add utils/nao_execute.py
git commit -m "Add nao_execute dispatcher for server-returned actions"
```

### Task 8.3: Add wake-hint extraction to `wake_listener.py`

**Files:**
- Modify: `wake_listener.py`

- [ ] **Step 1: Find the function returning a mode string**

Open `wake_listener.py`, find the dispatch that currently returns mode names like `"chat"`, `"chatbot"`, `"therapist"`, `"mini"`. Rename the mapping so it returns the server's hint vocabulary:

```python
_MODE_HINT_MAP = {
    "chat": "chat",
    "let's chat": "chat",
    "chatbot": "morgan",
    "morgan": "morgan",
    "morgan assist": "morgan",
    "therapist": "therapy",
    "therapist mode": "therapy",
    "therapy": "therapy",
    "mini nao": "skills",
    "mini": "skills",
}


def extract_hint(phrase):
    """Return one of chat/morgan/therapy/skills, or None if no match."""
    if not phrase: return None
    key = phrase.strip().lower()
    return _MODE_HINT_MAP.get(key)
```

Replace the existing dispatch return site so `listen_for_command` returns `(wake_phrase, hint)` where `hint` is the `extract_hint(...)` result.

- [ ] **Step 2: Commit**

```bash
git add wake_listener.py
git commit -m "Emit server-compatible hint (chat|morgan|therapy|skills|None) from wake listener"
```

### Task 8.4: Write `conversation.py`

**Files:**
- Create: `conversation.py`

- [ ] **Step 1: Write `conversation.py`**

```python
# -*- coding: utf-8 -*-
"""Single conversation loop. Replaces chat_mode, chatbot_mode, therapist_mode, mini_nao."""
from __future__ import print_function

import os
import time
import requests

from naoqi import ALProxy

import config
import audio_handler
from processing_announcer import ProcessingAnnouncer
from utils import face_naoqi, ask_name_utils, nao_execute, camera_capture, exit_detection
from utils.speech import expressive_say, time_of_day_greeting


_DEFAULT_TIMEOUT = 45


def _post(wav_path, img_path, username, hint, end_session=False):
    url = "http://{0}:5000/turn".format(config.SERVER_IP)
    files = {}
    if wav_path:
        files["audio"] = open(wav_path, "rb")
    if img_path:
        files["image"] = open(img_path, "rb")
    data = {"username": username or "guest"}
    if hint: data["hint"] = hint
    if end_session: data["end_session"] = "true"
    try:
        r = requests.post(url, files=files, data=data, timeout=_DEFAULT_TIMEOUT)
        return r.json() if r.status_code == 200 else None
    finally:
        for f in files.values(): f.close()


def _resolve_username(qi_session, tts, nao_ip):
    """Recognize face or ask for a name. Returns a string username."""
    name = face_naoqi.recognize_face_naoqi(qi_session, tts, timeout=4)
    if name:
        return name.lower()
    asked = ask_name_utils.ask_name(
        tts, nao_ip, "http://{0}:5000".format(config.SERVER_IP),
        qi_session, audio_handler.record_audio,
    )
    if asked and asked != "Guest":
        try: face_naoqi.learn_new_face_naoqi(qi_session, tts, asked)
        except Exception: pass
        return asked.lower()
    return "guest"


def run(qi_session, initial_hint=None):
    tts = ALProxy("ALAnimatedSpeech", config.NAO_IP, config.NAO_PORT)
    raw_tts = ALProxy("ALTextToSpeech", config.NAO_IP, config.NAO_PORT)
    motion = ALProxy("ALMotion", config.NAO_IP, config.NAO_PORT)
    posture = ALProxy("ALRobotPosture", config.NAO_IP, config.NAO_PORT)
    leds = ALProxy("ALLeds", config.NAO_IP, config.NAO_PORT)
    behav_mgr = ALProxy("ALBehaviorManager", config.NAO_IP, config.NAO_PORT)

    username = _resolve_username(qi_session, raw_tts, config.NAO_IP)
    expressive_say(raw_tts, "{0}, {1}".format(time_of_day_greeting(), username))

    suppress_image = False
    hint = initial_hint

    while True:
        wav = audio_handler.record_audio(config.NAO_IP)
        if not wav:
            continue

        img_path = None
        if not suppress_image:
            img_path = camera_capture.snap_quick(config.NAO_IP, config.NAO_PORT)

        ann = ProcessingAnnouncer(raw_tts)
        ann.start()
        try:
            resp = _post(wav, img_path, username, hint)
        finally:
            ann.stop()
            try:
                if wav and os.path.exists(wav): os.unlink(wav)
                if img_path and os.path.exists(img_path): os.unlink(img_path)
            except Exception: pass

        hint = None

        if resp is None:
            expressive_say(raw_tts, "My brain's not responding. Let's try again.")
            continue

        if resp.get("crisis"):
            expressive_say(raw_tts, resp.get("reply") or "")
            for action in resp.get("actions") or []:
                nao_execute.run(action, qi_session, motion, posture, leds, behav_mgr, raw_tts)
            break

        if resp.get("suppress_image"):
            suppress_image = True

        reply = resp.get("reply") or ""
        expressive_say(raw_tts, reply)

        for action in resp.get("actions") or []:
            nao_execute.run(action, qi_session, motion, posture, leds, behav_mgr, raw_tts)

        user_input = resp.get("user_input") or ""
        if exit_detection.detect_exit_intent(user_input):
            # Fire a final end_session call so the therapist recap can run server-side.
            try: _post(None, None, username, None, end_session=True)
            except Exception: pass
            expressive_say(raw_tts, "Take care.")
            break
```

- [ ] **Step 2: Commit**

```bash
git add conversation.py
git commit -m "Add conversation.py single-mode loop"
```

### Task 8.5: Update `main.py`

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Replace contents**

```python
# -*- coding: utf-8 -*-
"""NAO entry point. Wake loop → conversation.run(hint)."""
from __future__ import print_function

import qi

import config
import wake_listener
import conversation


def main():
    session = qi.Session()
    session.connect("tcp://{0}:{1}".format(config.NAO_IP, config.NAO_PORT))
    while True:
        phrase = wake_listener.listen_for_command()
        hint = wake_listener.extract_hint(phrase)
        try:
            conversation.run(session, initial_hint=hint)
        except KeyboardInterrupt:
            print("Exiting.")
            return
        except Exception as e:
            print("Conversation loop error:", e)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add main.py
git commit -m "Replace main.py with wake→conversation loop"
```

---

## Phase 9: Cutover

### Task 9.1: Delete obsolete files

**Files:**
- Delete: `chat_mode.py`, `chatbot_mode.py`, `therapist_mode.py`, `mini_nao.py`
- Delete: `gpt_handler.py`, `memory_manager.py`, `face_store.py`
- Delete: `memory.json`
- Delete: `utils/face_utils.py`, `utils/with_announcer.py`, `utils/file_utils.py`
- Delete: root-level `server.py` (replaced by `server/server.py`)
- Delete: `__pycache__/` tracked files if any

- [ ] **Step 1: Remove**

```bash
git rm chat_mode.py chatbot_mode.py therapist_mode.py mini_nao.py \
       gpt_handler.py memory_manager.py face_store.py memory.json \
       utils/face_utils.py utils/with_announcer.py utils/file_utils.py \
       server.py
git rm -rf __pycache__
```

- [ ] **Step 2: Search for any lingering imports**

```bash
grep -R "from gpt_handler\|import gpt_handler\|memory_manager\|face_store\|chat_mode\|chatbot_mode\|therapist_mode\|mini_nao\|face_utils\|with_announcer\|file_utils" --include='*.py' . || echo "clean"
```
Expected: `clean`.

- [ ] **Step 3: Run all server tests once more**

```bash
cd server && source .venv/bin/activate && pytest -v
```
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "Delete obsolete mode files, hand-rolled GPT handler, and dead utils"
```

### Task 9.2: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the "How to run" section** (or add one) with:

```markdown
## How to run

### Server (Python 3.11+)

    cd server
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    # Create .env with OPENAI_API_KEY, PINECONE_API_KEY, PINECONE_INDEX_NAME, etc.
    python -m server.server        # dev
    # or: gunicorn -w 1 -b 0.0.0.0:5000 server.server:app

### NAO robot (Python 2.7)

    ssh nao@<nao-ip>
    # copy the repo (excluding server/) into /home/nao/nao_assist
    export SERVER_IP=<your-server-ip>
    python /home/nao/nao_assist/main.py
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "Update README for server/ layout and /turn endpoint"
```

### Task 9.3: Update the Obsidian vault

**Files:**
- Modify (wiki):
  `~/Documents/Obsidian Vault/Nao-OpenAI-Morgan-Assist/wiki/log.md`,
  `~/Documents/Obsidian Vault/Nao-OpenAI-Morgan-Assist/wiki/index.md`,
  and the component/mode pages.

- [ ] **Step 1: Append to `log.md`**

```markdown
## 2026-MM-DD (fill in landing date)

**Agentic restructure landed.**

- Server rewritten onto OpenAI Agents SDK (`server/` package)
- Router + chat + chatbot + skills + therapist (+ cbt_coach, grounding_coach) agents
- Multimodal emotion via GPT-4o vision
- NAO side consolidated to one `conversation.py` loop
- Deleted: chat_mode, chatbot_mode, therapist_mode, mini_nao, gpt_handler, memory_manager, face_store, utils/face_utils, utils/with_announcer, utils/file_utils
- Rollback tag: `pre-agents-sdk`
```

- [ ] **Step 2: Update `index.md`** — remove dead links (Chat/Chatbot/Therapist/Mini NAO mode pages) or replace them with a single "Conversation Loop" page. Add new pages for Router/CBT Coach/Grounding Coach/Safety Gate/Agent Graph.

- [ ] **Step 3: Rewrite component/mode pages** in the vault:
  - Delete `wiki/modes/Chat Mode.md`, `wiki/modes/Chatbot Mode.md`, `wiki/modes/Therapist Mode.md`, `wiki/modes/Mini NAO Mode.md`.
  - Create `wiki/modes/Conversation Loop.md` summarizing the new unified `conversation.py`.
  - Create `wiki/components/Router Agent.md`, `wiki/components/Therapist Agent.md`, `wiki/components/CBT Coach.md`, `wiki/components/Grounding Coach.md`, `wiki/components/Skills Agent.md`, `wiki/components/Safety Gate.md`, `wiki/components/Actions Queue.md`.
  - Rewrite `wiki/components/Flask Server.md` to describe the `POST /turn` endpoint (replaces `/upload` + `/therapist_chat`).
  - Rewrite `wiki/components/GPT Handler.md` → rename file to `wiki/components/Agents SDK.md` and describe the SDK wiring.
  - Rewrite `wiki/components/Memory Manager.md` → rename to `wiki/components/Session Store.md` and describe SQLiteSession + consent + recaps.
  - Update `wiki/index.md` link list to match.

- [ ] **Step 4: Commit the vault**

This is outside the project git — Obsidian is a separate folder. No commit needed; Obsidian saves on write.

---

## Phase 10: End-to-end on hardware

### Task 10.1: Smoke-test against the real robot

**Files:** none

- [ ] **Step 1: Start server**

```bash
cd server && source .venv/bin/activate && python -m server.server
```

- [ ] **Step 2: Run `main.py` on NAO**

```bash
ssh nao@<ip>
export SERVER_IP=<server-ip>
python /home/nao/nao_assist/main.py
```

- [ ] **Step 3: Flow test**

Say each:
- "hey nao" → no hint → router should hand off after your first message
- "hey nao, morgan" → "what's CS 341 about?" → chatbot answers with CS context
- "hey nao, therapy" → "I've been really stressed" → therapist asks for camera consent, then engages
- "hey nao, skills" → "what's the weather" → skills answers

Watch server logs for handoffs and tool calls. Traces at `platform.openai.com/traces`.

- [ ] **Step 4: Exit test**

Mid-conversation say "goodbye" → NAO fires `end_session=true` → server runs recap for therapist users → NAO says "take care" → returns to wake listener.

---

## Plan Review Checklist (self-run before handing off)

- **Spec coverage:** Every spec section maps to at least one task (goals 1-7 → Phases 1-9; therapist deepened → Phases 5-6; multimodal emotion → Task 5.2 + 7.2; safety → Phase 3; camera consent → Task 5.2 + 7.2 + 8.4; session-end recap → Task 7.2 + 8.4).
- **Placeholders:** none (all code complete; no "add error handling").
- **Types/signatures:** `crisis_check → CrisisResult`, `pick_initial_agent(username, hint) → Agent`, `_run_agent → (reply, active, actions, suppress)` used consistently in `server.py` and its tests.

---

## Open items / risks

1. **Agents SDK version:** `openai-agents>=0.0.5` — API may evolve. If `handoff()`, `Runner.run()`, or `SQLiteSession` signatures change, reconcile during Task 6.
2. **SQLiteSession table names for `migrate_username`:** the test in Task 2.1 assumes the internal table is `agent_messages`. If the SDK uses a different name, update the UPDATE statement (or drive the merge through the SDK's public API: `new_session.add_items(old_session.get_items()); old_session.clear()`).
3. **Behavior names for `dance-<style>/behavior_1`:** these depend on which Choreographe behaviors are installed on this particular robot. May need to substitute locally-available behavior paths in Task 8.2.
4. **Token cost:** therapist uses gpt-4o with vision every turn. Budget-sensitive deployments can switch `THERAPIST_MODEL=gpt-4o-mini` (which also supports vision) to cut ~10×.
5. **Whisper retry:** the new `_transcribe()` is a single unretrying call. The old code had a retry wrapper. If flaky, wrap `_transcribe` in a small retry loop (3 attempts, exponential backoff from 0.8s) in Task 7.2 — kept out of the initial implementation to stay YAGNI.
