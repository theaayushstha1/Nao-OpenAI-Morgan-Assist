# Alive Mode Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship proactive greetings + sentence-streaming replies + hierarchical memory on top of the existing `refactor/agents-sdk` branch. Spec: `docs/superpowers/specs/2026-04-14-alive-mode-design.md`.

**Branch:** create `feature/alive-mode` off `refactor/agents-sdk`.

**Waves:**
- Wave A (parallel, independent): Tasks 1, 2, 3 — pure server additions
- Wave B (parallel, independent): Tasks 4, 5 — pure NAO additions
- Wave C: Task 6 — integration in `nao/main.py`
- Wave D: Task 7 — end-to-end smoke test

---

## Task 1 — Hierarchical memory tables + rollup

**Files:**
- Modify: `server/session.py` — add `weekly_themes`, `monthly_personas` tables + getters/setters
- Create: `server/memory_rollup.py` — triggered rollup logic
- Create: `server/tests/test_memory_rollup.py`

- [ ] **Step 1: Failing tests**

```python
# server/tests/test_memory_rollup.py
from unittest.mock import patch
from datetime import datetime, timedelta
from server import session as s
from server import memory_rollup as r


def test_weekly_rollup_fires_on_third_recap(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "_DB_PATH", str(tmp_path / "db"))
    with patch.object(r, "_summarize_to_theme", return_value="Stress about finals this week."):
        s.save_recap("alice", "session 1")
        s.save_recap("alice", "session 2")
        s.save_recap("alice", "session 3")
        r.maybe_rollup_week("alice")
    themes = r.load_week_themes("alice")
    assert len(themes) == 1
    assert "finals" in themes[0]


def test_weekly_rollup_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "_DB_PATH", str(tmp_path / "db"))
    with patch.object(r, "_summarize_to_theme", return_value="theme"):
        for i in range(5):
            s.save_recap("bob", f"session {i}")
            r.maybe_rollup_week("bob")
    themes = r.load_week_themes("bob")
    assert len(themes) == 1  # still only one theme for the week


def test_monthly_rollup_fires_on_second_weekly_theme(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "_DB_PATH", str(tmp_path / "db"))
    r._save_theme("alice", datetime.now(), "week 1 theme")
    r._save_theme("alice", datetime.now() + timedelta(days=7), "week 2 theme")
    with patch.object(r, "_summarize_to_persona", return_value="Growing through finals stress."):
        r.maybe_rollup_month("alice")
    personas = r.load_month_personas("alice")
    assert len(personas) == 1
    assert "Growing" in personas[0]
```

- [ ] **Step 2: Extend `server/session.py`**

Add to `_conn()` setup:
```python
c.execute("CREATE TABLE IF NOT EXISTS weekly_themes ("
          "id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, "
          "week_start DATE NOT NULL, body TEXT NOT NULL, "
          "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
          "UNIQUE(username, week_start))")
c.execute("CREATE TABLE IF NOT EXISTS monthly_personas ("
          "id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, "
          "month DATE NOT NULL, body TEXT NOT NULL, "
          "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
          "UNIQUE(username, month))")
```

- [ ] **Step 3: Create `server/memory_rollup.py`**

```python
"""Weekly and monthly rollups of therapist session recaps."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from openai import OpenAI

from server import config, session as s

_client = OpenAI(api_key=config.OPENAI_API_KEY)


def _week_start(d: date | None = None) -> str:
    d = d or date.today()
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


def _month_start(d: date | None = None) -> str:
    d = d or date.today()
    return d.replace(day=1).isoformat()


def _summarize_to_theme(recaps: list[str]) -> str:
    joined = "\n- ".join(recaps)
    r = _client.chat.completions.create(
        model=config.CRISIS_MODEL,
        messages=[
            {"role": "system", "content":
             "Summarize this week's therapy session recaps into 1-2 sentences. "
             "Focus on recurring themes, growth, or stuck points. Warm, non-clinical tone."},
            {"role": "user", "content": f"- {joined}"},
        ],
        temperature=0.3,
    )
    return r.choices[0].message.content.strip()


def _summarize_to_persona(themes: list[str]) -> str:
    joined = "\n- ".join(themes)
    r = _client.chat.completions.create(
        model=config.CRISIS_MODEL,
        messages=[
            {"role": "system", "content":
             "Summarize this month's weekly themes into a short user persona: "
             "2-3 sentences capturing who this person has been lately, what matters to them. "
             "Warm, observational, non-clinical."},
            {"role": "user", "content": f"- {joined}"},
        ],
        temperature=0.4,
    )
    return r.choices[0].message.content.strip()


def _save_theme(username: str, when: datetime, body: str) -> None:
    with s._conn() as c:
        c.execute("INSERT OR IGNORE INTO weekly_themes (username, week_start, body) "
                  "VALUES (?, ?, ?)", (username, _week_start(when.date()), body))


def _save_persona(username: str, when: datetime, body: str) -> None:
    with s._conn() as c:
        c.execute("INSERT OR IGNORE INTO monthly_personas (username, month, body) "
                  "VALUES (?, ?, ?)", (username, _month_start(when.date()), body))


def maybe_rollup_week(username: str) -> None:
    """If ≥3 recaps this week and no theme yet, generate and save the theme."""
    week_start = _week_start()
    with s._conn() as c:
        existing = c.execute(
            "SELECT 1 FROM weekly_themes WHERE username=? AND week_start=?",
            (username, week_start)).fetchone()
        if existing:
            return
        rows = c.execute(
            "SELECT body FROM recaps WHERE username=? "
            "AND DATE(created_at) >= DATE(?) ORDER BY id",
            (username, week_start)).fetchall()
    if len(rows) < 3:
        return
    theme = _summarize_to_theme([r[0] for r in rows])
    _save_theme(username, datetime.now(), theme)


def maybe_rollup_month(username: str) -> None:
    month_start = _month_start()
    with s._conn() as c:
        existing = c.execute(
            "SELECT 1 FROM monthly_personas WHERE username=? AND month=?",
            (username, month_start)).fetchone()
        if existing:
            return
        rows = c.execute(
            "SELECT body FROM weekly_themes WHERE username=? "
            "AND DATE(week_start) >= DATE(?) ORDER BY id",
            (username, month_start)).fetchall()
    if len(rows) < 2:
        return
    persona = _summarize_to_persona([r[0] for r in rows])
    _save_persona(username, datetime.now(), persona)


def load_week_themes(username: str, n: int = 1) -> list[str]:
    with s._conn() as c:
        rows = c.execute(
            "SELECT body FROM weekly_themes WHERE username=? "
            "ORDER BY id DESC LIMIT ?", (username, n)).fetchall()
    return [r[0] for r in rows]


def load_month_personas(username: str, n: int = 1) -> list[str]:
    with s._conn() as c:
        rows = c.execute(
            "SELECT body FROM monthly_personas WHERE username=? "
            "ORDER BY id DESC LIMIT ?", (username, n)).fetchall()
    return [r[0] for r in rows]
```

- [ ] **Step 4: Hook rollup into recap save**

In `server/tools/emotion.py::_recap_session_impl`, after `session.save_recap(username, body)` add:
```python
from server import memory_rollup
memory_rollup.maybe_rollup_week(username)
memory_rollup.maybe_rollup_month(username)
```

- [ ] **Step 5: Inject memory into therapist system prompt**

In `server/agents/therapist.py::build_therapist_agent`, after the existing recap block add:
```python
from server import memory_rollup as mr
week_themes = mr.load_week_themes(username, n=1)
month_personas = mr.load_month_personas(username, n=1)
wk = f"\n\nThis week's theme:\n- {week_themes[0]}" if week_themes else ""
mo = f"\n\nThis month's persona:\n{month_personas[0]}" if month_personas else ""
# add wk + mo to instructions alongside recap_block
```

- [ ] **Step 6: Run tests**

```bash
source server/.venv/bin/activate && python -m pytest server/tests/test_memory_rollup.py -v
```
All 3 must pass.

- [ ] **Step 7: Commit**

```bash
git add server/session.py server/memory_rollup.py server/tools/emotion.py server/agents/therapist.py server/tests/test_memory_rollup.py
git commit -m "Add hierarchical memory (weekly themes + monthly personas) with triggered rollups"
```

---

## Task 2 — Streaming turn endpoint (SSE)

**Files:**
- Modify: `server/server.py` — add `/stream_turn` route
- Create: `server/streaming.py` — sentence splitter + SSE helpers
- Create: `server/tests/test_streaming.py`

- [ ] **Step 1: Failing tests**

```python
# server/tests/test_streaming.py
from server.streaming import iter_sentences


def test_splits_on_period():
    chunks = ["Hel", "lo there", ". How are you", "?"]
    out = list(iter_sentences(iter(chunks)))
    assert out == ["Hello there.", "How are you?"]


def test_preserves_abbreviations():
    chunks = ["Dr. Wang is great. See you."]
    out = list(iter_sentences(iter(chunks)))
    assert out == ["Dr. Wang is great.", "See you."]


def test_flushes_trailing_without_terminator():
    chunks = ["no terminator"]
    out = list(iter_sentences(iter(chunks)))
    assert out == ["no terminator"]
```

- [ ] **Step 2: Implement `server/streaming.py`**

```python
"""SSE helpers: split a stream of text chunks into complete sentences."""
from __future__ import annotations

import re
from typing import Iterable, Iterator

# Common abbreviations that shouldn't end a sentence.
_ABBR = {"dr.", "mr.", "mrs.", "ms.", "prof.", "e.g.", "i.e.", "etc.", "vs.", "st.", "no."}


def iter_sentences(chunks: Iterable[str]) -> Iterator[str]:
    """Yield complete sentences from a stream of text fragments."""
    buf = ""
    for chunk in chunks:
        buf += chunk
        while True:
            # Find a sentence-ender; skip if preceded by a known abbreviation.
            m = re.search(r"[.!?](\s|$)", buf)
            if not m:
                break
            end = m.end() - (1 if m.group(1) == "" else 1)
            candidate = buf[:end].strip()
            lower = candidate.lower()
            if any(lower.endswith(a) for a in _ABBR):
                break
            yield candidate
            buf = buf[m.end():].lstrip()
    if buf.strip():
        yield buf.strip()
```

- [ ] **Step 3: Add `/stream_turn` route to `server/server.py`**

```python
import json as _json

@app.post("/stream_turn")
def stream_turn():
    """Same inputs as /turn, responds as Server-Sent Events with per-sentence chunks."""
    username = request.form.get("username") or "guest"
    hint = request.form.get("hint") or None
    audio = request.files.get("audio")
    image = request.files.get("image")

    if not audio:
        return jsonify(error="missing_audio"), 400

    # Save + validate + transcribe (same as /turn)
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
    consent = session.get_camera_consent(username)
    image_b64 = base64.b64encode(image.read()).decode("ascii") if image and consent else None

    def generate():
        if crisis.positive:
            yield _sse({"type": "sentence", "text": safety.HOTLINE_REPLY})
            yield _sse({"type": "action", "action": {"name": "change_eye_color", "args": {"color": "white"}}})
            yield _sse({"type": "done", "active_agent": "safety", "crisis": True, "suppress_image": False, "user_input": transcript})
            return

        agent = pick_initial_agent(username, hint)
        sess = session.get_or_create_session(username)
        ctx = {
            "username": username, "actions_queue": [], "emotion_log": [],
            "latest_image_b64": image_b64, "suppress_image": False,
        }
        message = _build_user_message(transcript, image_b64)

        async def run_stream():
            result = Runner.run_streamed(agent, message, context=ctx, session=sess)
            async for ev in result.stream_events():
                if ev.type == "raw_response_event" and hasattr(ev.data, "delta") and ev.data.delta:
                    yield ev.data.delta
            # After complete, yield remaining ctx
            return result

        # Bridge async to sync iterator
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            gen = run_stream()
            agen = gen.__aiter__()
            full_text_chunks = []
            while True:
                try:
                    chunk = loop.run_until_complete(agen.__anext__())
                except StopAsyncIteration:
                    break
                full_text_chunks.append(chunk)

            from server.streaming import iter_sentences
            for sent in iter_sentences(iter(full_text_chunks)):
                yield _sse({"type": "sentence", "text": sent})
            for action in ctx["actions_queue"]:
                yield _sse({"type": "action", "action": action})
            yield _sse({"type": "done", "active_agent": agent.name,
                         "crisis": False, "suppress_image": bool(ctx["suppress_image"]),
                         "user_input": transcript})
        finally:
            loop.close()

    from flask import Response
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(obj) -> str:
    return f"data: {_json.dumps(obj)}\n\n"
```

**Note on Agents SDK streaming:** `Runner.run_streamed()` returns a streaming result. If the API signature differs in 0.13.6, fall back to running the non-streaming `Runner.run()` and splitting the returned `final_output` into sentences. Document the fallback path in the report.

- [ ] **Step 4: Tests pass, commit**

```bash
pytest server/tests/test_streaming.py -v
git add server/streaming.py server/server.py server/tests/test_streaming.py
git commit -m "Add /stream_turn SSE endpoint with per-sentence streaming"
```

---

## Task 3 — `/greet` endpoint (proactive greeting)

**Files:**
- Modify: `server/server.py` — add `/greet` route
- Modify: `server/session.py` — add `proactive_enabled` pref
- Create: `server/tests/test_greet.py`

- [ ] **Step 1: Failing test**

```python
# server/tests/test_greet.py
import io
from unittest.mock import patch
import pytest
from server.server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


def test_greet_streams_greeting(client):
    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # minimal JPEG header
    with patch("server.server._recognize_face", return_value="alice"), \
         patch("server.server._generate_greeting", return_value=iter(
             ["Hey Alice!", "How's the week been?"])):
        r = client.post("/greet", data={
            "image": (io.BytesIO(fake_jpeg), "face.jpg"),
        }, content_type="multipart/form-data")
    assert r.status_code == 200
    assert r.mimetype == "text/event-stream"
    body = r.get_data(as_text=True)
    assert "Hey Alice!" in body
    assert "alice" in body


def test_greet_with_proactive_disabled(client, monkeypatch):
    from server import session
    monkeypatch.setattr(session, "get_proactive_enabled", lambda u: False)
    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    with patch("server.server._recognize_face", return_value="alice"):
        r = client.post("/greet", data={
            "image": (io.BytesIO(fake_jpeg), "face.jpg"),
        }, content_type="multipart/form-data")
    body = r.get_data(as_text=True)
    assert "skipped" in body.lower() or r.status_code == 204
```

- [ ] **Step 2: Add pref to session**

In `server/session.py`, extend `user_prefs` to have `proactive_enabled` column:
```python
c.execute("ALTER TABLE user_prefs ADD COLUMN proactive_enabled INTEGER DEFAULT 1")
# wrap in try/except since it fails on re-run; use _table_has_column helper
```
Add helpers `get_proactive_enabled(username) -> bool` and `set_proactive_enabled(username, bool)`.

- [ ] **Step 3: Add `/greet` route**

```python
@app.post("/greet")
def greet():
    image = request.files.get("image")
    if not image:
        return jsonify(error="missing_image"), 400

    image_bytes = image.read()
    username = _recognize_face(image_bytes) or "guest"

    if not session.get_proactive_enabled(username):
        def skipped():
            yield _sse({"type": "done", "active_agent": "none", "skipped": True})
        from flask import Response
        return Response(skipped(), mimetype="text/event-stream")

    def generate():
        yield _sse({"type": "recognized", "username": username})
        for sent in _generate_greeting(username, image_bytes):
            yield _sse({"type": "sentence", "text": sent})
        yield _sse({"type": "done", "active_agent": "therapist", "username": username})

    from flask import Response
    return Response(generate(), mimetype="text/event-stream")


def _recognize_face(image_bytes: bytes) -> str | None:
    """Stub for face reco via server. For v1, returns 'guest'.
    Real face reco happens NAO-side via ALFaceDetection; this endpoint trusts
    a `username` form field if provided as a shortcut.
    """
    # TODO: real server-side face reco. For now accept username form hint.
    return request.form.get("username") or None


def _generate_greeting(username: str, image_bytes: bytes):
    """Generate a 1-2 sentence personalized greeting. Yields sentences."""
    from server.agents.therapist import build_therapist_agent
    import asyncio, base64
    from agents import Runner

    agent = build_therapist_agent(username)
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    ctx = {
        "username": username, "actions_queue": [], "emotion_log": [],
        "latest_image_b64": image_b64, "suppress_image": False,
    }
    prompt_msg = [
        {"type": "text", "text": "The user just walked up. You can see their face. Greet them in ONE sentence, personalized with their name and any relevant memory. Do not ask a question yet."},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
    ]
    result = asyncio.run(Runner.run(agent, prompt_msg, context=ctx))
    from server.streaming import iter_sentences
    yield from iter_sentences(iter([result.final_output]))
```

- [ ] **Step 4: Tests pass, commit**

```bash
pytest server/tests/test_greet.py -v
git add server/session.py server/server.py server/tests/test_greet.py
git commit -m "Add /greet SSE endpoint for proactive greetings + proactive_enabled pref"
```

---

## Task 4 — NAO-side perception watcher

**Files:**
- Create: `nao/perceive.py`

- [ ] **Step 1: Create `nao/perceive.py`**

```python
# -*- coding: utf-8 -*-
"""Watch for people entering NAO's view and invoke a callback."""
from __future__ import print_function

import time
import threading


class Watcher(object):
    """Subscribes to ALPeoplePerception and fires on_person(face_jpeg_path)."""

    def __init__(self, qi_session, camera_capture, on_person, debounce_sec=2.0):
        self.session = qi_session
        self.camera = camera_capture  # nao.utils.camera_capture module
        self.on_person = on_person
        self.debounce_sec = debounce_sec
        self._last_seen = 0.0
        self._stop = threading.Event()
        self._thread = None

    def start(self, nao_ip, nao_port=9559):
        self.nao_ip = nao_ip
        self.nao_port = nao_port
        self._stop.clear()
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self):
        memory = self.session.service("ALMemory")
        people = self.session.service("ALPeoplePerception")
        try:
            people.subscribe("alive_mode")
        except Exception as e:
            print("[perceive] could not subscribe ALPeoplePerception:", e)
            return
        try:
            while not self._stop.is_set():
                try:
                    ids = memory.getData("PeoplePerception/PeopleList")
                except Exception:
                    ids = None
                now = time.time()
                if ids and (now - self._last_seen) > self.debounce_sec:
                    self._last_seen = now
                    img = self.camera.snap_quick(self.nao_ip, self.nao_port)
                    if img:
                        try:
                            self.on_person(img)
                        except Exception as e:
                            print("[perceive] callback error:", e)
                time.sleep(0.5)
        finally:
            try:
                people.unsubscribe("alive_mode")
            except Exception:
                pass
```

- [ ] **Step 2: Syntax check + commit**

```bash
python3 -c "import ast; ast.parse(open('nao/perceive.py').read()); print('ok')"
git add nao/perceive.py
git commit -m "Add NAO perception watcher (ALPeoplePerception + debounced snap)"
```

---

## Task 5 — NAO-side streaming TTS consumer

**Files:**
- Create: `nao/stream_tts.py`

- [ ] **Step 1: Create `nao/stream_tts.py`**

```python
# -*- coding: utf-8 -*-
"""Consume an SSE stream of sentences/actions and speak/execute in order."""
from __future__ import print_function

import json
import threading
import requests


def consume(sse_url, files, data, tts, on_action, on_done, timeout=120):
    """POST to sse_url, stream SSE events, speak sentences, execute actions.

    tts: ALTextToSpeech proxy.
    on_action(action_dict): called per action event.
    on_done(info_dict): called once with final info.
    Returns the final info dict (also passed to on_done).
    """
    headers = {"Accept": "text/event-stream"}
    resp = requests.post(sse_url, files=files, data=data, headers=headers,
                         stream=True, timeout=timeout)
    if resp.status_code != 200:
        return {"error": "http_{0}".format(resp.status_code)}

    final = {}
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data: "):
            continue
        try:
            ev = json.loads(raw[6:])
        except Exception:
            continue
        etype = ev.get("type")
        if etype == "sentence":
            try:
                tts.say(_sayable(ev.get("text", "")))
            except Exception as e:
                print("[stream_tts] say error:", e)
        elif etype == "action":
            try:
                on_action(ev.get("action") or {})
            except Exception as e:
                print("[stream_tts] action error:", e)
        elif etype == "done":
            final = ev
            break
        elif etype == "recognized":
            final["username"] = ev.get("username")
    on_done(final)
    return final


def _sayable(text):
    if isinstance(text, unicode):  # noqa: F821  (Py2.7)
        return text.encode("utf-8", "ignore")
    return str(text)
```

- [ ] **Step 2: Syntax check + commit**

```bash
python3 -c "import ast; ast.parse(open('nao/stream_tts.py').read()); print('ok')"
git add nao/stream_tts.py
git commit -m "Add NAO SSE consumer that speaks sentences + dispatches actions"
```

---

## Task 6 — Integrate passive mode in `nao/main.py`

**Files:**
- Modify: `nao/main.py`
- Modify: `nao/conversation.py` — add `run_streaming(qi_session, initial_hint=None)` variant

- [ ] **Step 1: Add streaming variant to `nao/conversation.py`**

Below the existing `run()`, add `run_streaming()` which:
- Same setup as `run()`
- Replaces `_post` + `expressive_say` with `stream_tts.consume()` against `/stream_turn`
- Everything else identical

Full code (append to conversation.py):
```python
from nao import stream_tts


def run_streaming(qi_session, initial_hint=None):
    """Streaming variant: sentences arrive and are spoken as they're generated."""
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

        files = {}
        if wav: files["audio"] = open(wav, "rb")
        if img_path: files["image"] = open(img_path, "rb")
        data = {"username": username}
        if hint: data["hint"] = hint

        def handle_action(action):
            nao_execute.run(action, qi_session, motion, posture, leds, behav_mgr, raw_tts)
        def handle_done(info):
            pass

        url = "http://{0}:5000/stream_turn".format(config.SERVER_IP)
        info = stream_tts.consume(url, files, data, raw_tts, handle_action, handle_done)

        for f in files.values(): f.close()
        try:
            if wav and os.path.exists(wav): os.unlink(wav)
            if img_path and os.path.exists(img_path): os.unlink(img_path)
        except Exception:
            pass

        hint = None
        if info.get("crisis"):
            break
        if info.get("suppress_image"):
            suppress_image = True
        user_input = info.get("user_input") or ""
        if exit_detection.detect_exit_intent(user_input):
            try:
                requests.post("http://{0}:5000/turn".format(config.SERVER_IP),
                              data={"username": username, "end_session": "true"}, timeout=10)
            except Exception:
                pass
            expressive_say(raw_tts, "Take care.")
            break
```

- [ ] **Step 2: Update `nao/main.py`** — wire perception + passive loop

```python
# -*- coding: utf-8 -*-
"""NAO entry point. Passive perception + wake-word dispatch."""
from __future__ import print_function

import threading
import qi
import requests

import config
import wake_listener
import conversation
from perceive import Watcher
from utils import camera_capture
from naoqi import ALProxy


_engaged = threading.Event()  # set when NAO is in a conversation


def _on_person_seen(jpeg_path):
    """Proactive entry: called when a person is detected. Opens /greet SSE."""
    if _engaged.is_set():
        return
    _engaged.set()
    try:
        raw_tts = ALProxy("ALTextToSpeech", config.NAO_IP, config.NAO_PORT)
        from stream_tts import consume
        url = "http://{0}:5000/greet".format(config.SERVER_IP)
        files = {"image": open(jpeg_path, "rb")}
        data = {}
        def noop_action(_): pass
        def on_done(_): pass
        consume(url, files, data, raw_tts, noop_action, on_done, timeout=60)
        files["image"].close()
    except Exception as e:
        print("[proactive] error:", e)
    finally:
        _engaged.clear()


def main():
    session = qi.Session()
    session.connect("tcp://{0}:{1}".format(config.NAO_IP, config.NAO_PORT))

    watcher = Watcher(session, camera_capture, _on_person_seen)
    watcher.start(config.NAO_IP, config.NAO_PORT)

    try:
        while True:
            phrase = wake_listener.listen_for_command(config.NAO_IP, config.NAO_PORT)
            hint = wake_listener.extract_hint(phrase)
            _engaged.set()
            try:
                conversation.run_streaming(session, initial_hint=hint)
            except KeyboardInterrupt:
                print("Exiting.")
                return
            except Exception as e:
                print("Conversation loop error:", e)
            finally:
                _engaged.clear()
    finally:
        watcher.stop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Parse check + commit**

```bash
python3 -c "import ast; [ast.parse(open(f).read()) for f in ['nao/main.py', 'nao/conversation.py']]; print('ok')"
git add nao/main.py nao/conversation.py
git commit -m "Wire passive perception watcher + streaming conversation loop"
```

---

## Task 7 — End-to-end smoke test

- [ ] **Step 1: Server sanity**

```bash
source server/.venv/bin/activate && python -m pytest -q
# all tests must pass
```

- [ ] **Step 2: Start server**

```bash
SERVER_PORT=5001 python -m server.server &
sleep 2
curl -s localhost:5001/health
```

- [ ] **Step 3: Smoke-test `/stream_turn`**

```bash
curl -N -X POST localhost:5001/stream_turn \
  -F "audio=@server/tests/fixtures/sample.wav" \
  -F "username=smoketest" -F "hint=skills"
# Should print multiple `data: {...}` SSE events
```

- [ ] **Step 4: Smoke-test `/greet`**

```bash
curl -N -X POST localhost:5001/greet \
  -F "image=@server/tests/fixtures/sample.wav" \
  -F "username=smoketest"
# Should print sentences streaming in
```

- [ ] **Step 5: On-robot (manual, after shipping)**

```bash
scp -r nao/ nao@172.20.95.121:/home/nao/nao_assist/
ssh nao@172.20.95.121
SERVER_IP=<mac IP> python /home/nao/nao_assist/main.py
# Walk up to NAO -> it should greet you without wake word
# Say "stop" -> returns to passive
```

---

## Open items

1. **Agents SDK streaming API shape** — `Runner.run_streamed()` signature in 0.13.6 may need verification. Fallback: run non-streaming then sentence-split the final output (loses streaming benefit but still works).
2. **Face reco on server** — v1 uses a username form-hint; proper server-side dlib face reco can come later.
3. **Proactive debounce tuning** — 2s default; may need adjustment on real hardware.
