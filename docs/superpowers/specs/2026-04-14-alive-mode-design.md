# Alive Mode Design (A+B combined)

**Date:** 2026-04-14
**Author:** Aayush Shrestha (with Claude)
**Status:** Draft
**Scope:** Proactive engagement + streaming TTS + hierarchical memory. Publishable research angle: *proactive embodied agents with longitudinal multi-timescale memory*.

## Goals

1. **Proactive greetings.** NAO watches the space via `ALPeoplePerception` when idle. When it detects an approaching person, it recognizes their face and speaks a personalized, memory-aware greeting without being woken by a phrase.
2. **Perceived latency <500ms** on replies. Stream GPT text output sentence-by-sentence; each completed sentence is handed to NAO's TTS while the next streams in. No external TTS service (no ElevenLabs subscription needed).
3. **Hierarchical memory.** Therapist agent's system prompt is augmented with three timescales:
   - Level 1: last 3 session recaps (already exists)
   - Level 2: current week's theme (rollup of week's sessions)
   - Level 3: current month's persona (rollup of weekly themes)
4. **Clean opt-in.** Proactive mode can be disabled per-user. Default: on.

## Non-Goals

- Voice cloning or neural TTS (NAO's built-in TTS is the sink for v1).
- Real audio streaming (PCM over WebRTC). We stream *text sentences*; each is synthesized locally by NAO's TTS.
- Autonomous motion / navigation. NAO stays put during proactive greeting.

## Architecture

### New components

```
NAO (Py 2.7)                                 Server (Py 3.11+)
nao/perceive.py        ────HTTP POST────▶   /greet       (personalized greeting by face)
  ALPeoplePerception loop                   /stream_turn (SSE: streamed sentences)
  Emit "person_seen" with face enc
                                             server/memory_rollup.py (hierarchical)
nao/stream_tts.py       ◀────SSE stream─    
  Consume sentence chunks                    server/agents/therapist.py
  NAO TTS speaks each                          (reads L1+L2+L3 memory)
```

### Proactive engagement flow

1. **Passive loop** (`nao/main.py`): when not in a conversation, start `perceive.watch()` in a thread and idle the wake listener in parallel.
2. **Detection**: `ALPeoplePerception` emits `PeoplePerception/JustArrived` with person IDs. When a new person is present for ≥2 consecutive frames (debounce), `perceive` captures a face JPEG and calls the server's `POST /greet` with the image.
3. **Recognition**: server runs face reco via existing path → returns username (or "guest").
4. **Greeting synthesis**: server looks up Level 1/2/3 memory, asks the therapist agent (in a single non-session turn) to produce a 1-2 sentence personalized greeting, streams it via SSE.
5. **Speech**: NAO's `stream_tts` consumer reads each sentence from the SSE stream and hands it to `ALTextToSpeech.say()` in order. First sentence usually speaks within 500ms of detection.
6. **Transition**: after greeting, NAO enters the normal `conversation.run()` loop with an implicit `hint=None` (router handles the intent).

### Streaming TTS pattern

We stream **text sentences**, not audio bytes. NAO's TTS renders each sentence locally and serially. The server splits GPT output at `.`, `?`, `!` (with look-ahead to avoid breaking on "e.g." or "Mr.").

Server endpoint `POST /stream_turn` uses Server-Sent Events. Event types:
- `data: {"type":"sentence","text":"..."}`
- `data: {"type":"action","action":{"name":"...","args":{...}}}`
- `data: {"type":"done","active_agent":"...","suppress_image":bool,"crisis":bool}`

NAO speaks each `sentence` as it arrives. Actions are queued and executed after the final sentence (or interleaved if a sentence explicitly pairs with one — future work).

Regular `POST /turn` stays as-is for backward compat. `conversation.py` gets an `--stream` flag that uses the new endpoint.

### Hierarchical memory

Schema (extends `server/session.py`):

```sql
CREATE TABLE recaps (id INTEGER PRIMARY KEY, username TEXT, body TEXT, created_at TIMESTAMP);
CREATE TABLE weekly_themes (id INTEGER PRIMARY KEY, username TEXT, week_start DATE, body TEXT, created_at TIMESTAMP);
CREATE TABLE monthly_personas (id INTEGER PRIMARY KEY, username TEXT, month DATE, body TEXT, created_at TIMESTAMP);
```

Rollup triggers (in `server/memory_rollup.py`):
- **Weekly**: when a new recap lands, if ≥3 recaps exist in the current ISO week and no theme exists yet, GPT summarizes them → `weekly_themes`. Runs on recap write, not on a timer, so zero ops.
- **Monthly**: when a weekly theme lands and ≥2 weekly themes exist in the current calendar month and no persona exists yet, GPT summarizes → `monthly_personas`.

Therapist system prompt injection (in `server/agents/therapist.py`):
```
Recent sessions (last 3):
- {recap_1}
- {recap_2}
- {recap_3}

This week's themes:
- {week_theme}

This month's persona:
{monthly_persona}
```

Only non-empty sections are included. First-time users see only what exists.

## Wire format changes

### `POST /greet` (new)
Multipart:
- `image` (JPEG) — face for recognition
Response:
```json
{
  "username": "alice",
  "greeting_sse_url": "/stream_turn?token=<one-shot>&username=alice&hint=greeting"
}
```
NAO then opens SSE on that URL.

### `POST /stream_turn` (new)
Same multipart as `/turn` plus streams response as SSE. A short-lived token path allows NAO to stream without re-uploading audio/image.

Actually simpler: combine into one call — `POST /stream_turn` takes the same form fields as `/turn`; the response IS the SSE stream. No token needed.

### `POST /greet` response streams directly
```
POST /greet  (multipart: image, username?)
→ 200 OK, Content-Type: text/event-stream
→ data: {"type":"recognized","username":"alice"}
→ data: {"type":"sentence","text":"Hey Alice, last time we talked about finals..."}
→ data: {"type":"sentence","text":"How did those go?"}
→ data: {"type":"done","active_agent":"therapist"}
```

This is cleaner — NAO opens one request and streams everything.

## Perception loop

NAO-side module `nao/perceive.py`:

```python
class Watcher:
    def __init__(self, qi_session, on_person_seen):
        self.memory = qi_session.service("ALMemory")
        self.people = qi_session.service("ALPeoplePerception")
        self.on_person_seen = on_person_seen

    def start(self):
        self.subscriber = self.memory.subscriber("PeoplePerception/JustArrived")
        self.subscriber.signal.connect(self._on_event)

    def _on_event(self, value):
        # value is a list of person IDs; debounce, grab face JPEG, invoke callback
        ...
```

The callback (`on_person_seen`) is whatever NAO wants to do — in our case, snap a photo via `ALPhotoCapture`, open an SSE stream to `/greet`, speak what comes back.

## Safety

- Crisis check still runs on the first user turn after a proactive greeting (pre-dispatch, unchanged).
- Proactive greetings never touch sensitive topics — the therapist agent is prompted to stay surface-level for the proactive turn. Detailed memory comes up only if the user opens that door.
- Per-user `proactive_enabled` flag in `user_prefs` (default true). Command phrase "don't greet me" sets it to false.

## Research angle

The combination is novel: *proactive embodied agent with multi-timescale episodic memory, measured in a real student-population deployment*. Previous HRI work has looked at either (a) proactive behavior with short-term memory, or (b) long-term memory with reactive-only agents. Combining them in an always-available CS-department context (Morgan State, HBCU population) is an under-studied niche.

Publication target: **HRI 2027** full paper — system + longitudinal study.

## Rollout

1. Branch `feature/alive-mode` off `refactor/agents-sdk`.
2. Build server-side (`/greet`, `/stream_turn`, memory rollup) behind feature flags.
3. Build NAO-side (`perceive.py`, `stream_tts.py`) as new files; `conversation.py` gains a streaming mode.
4. Integration test on-robot with real face, real conversation.
5. 1-week soft deployment at a CS department common area; collect metrics (detections/day, false positives, session counts).
6. Merge to `main` only after on-robot validation.

## Open Questions

Answered during brainstorming; none remain.
