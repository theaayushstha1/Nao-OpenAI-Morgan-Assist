# Agentic Restructure Design

**Date:** 2026-04-13
**Author:** Aayush Shrestha (with Claude)
**Status:** Draft — awaiting implementation plan
**Scope:** Server-side rewrite onto OpenAI Agents SDK + multi-agent routing; NAO-side consolidation from 4 mode files to 1 loop; vision-based emotion detection; therapist CBT + grounding sub-agents

## Goals

1. Replace the hand-rolled GPT handler with the **OpenAI Agents SDK** (Python 3 `openai-agents` package) so routing, handoffs, sessions, and tool-calling are framework-native instead of custom code.
2. Collapse NAO-side from `chat_mode.py`, `chatbot_mode.py`, `therapist_mode.py`, `mini_nao.py` (~1300 lines) to a single `conversation.py` loop (~150 lines). All mode-specific logic moves to server.
3. Fold `mini_nao` skills (time, weather, timers, reminders, todos) into a server-side `skills` agent with tools.
4. Upgrade therapist mode into a **CBT/grounding-aware agent system** with specialist sub-agents, CBT distortion detection, thought records, grounding exercises, and safety-first crisis handling.
5. Add **multimodal emotion detection** — NAO snaps a JPEG per turn; the therapist agent (and optionally others) see the user's face and words together through GPT-4o vision.
6. Cut dead code, duplicate helpers, and unused modules (`face_store.py`, `utils/face_utils.py`, `utils/with_announcer.py`, `gpt_handler.py`, `memory_manager.py`, etc.).
7. Keep the Python 2.7 ↔ Python 3 split — NAO side stays 2.7 (naoqi constraint), server stays 3.11+.

## Non-Goals

- Moving NAO to Python 3 (firmware constraint).
- Replacing Whisper or Pinecone (they work fine).
- Adding streaming TTS (NAO speaks whole utterances).
- Running a fully local LLM on-robot.
- Rewriting the wake-word system beyond adding the optional hint.

## Architecture

### High-level topology

```
NAO (Py 2.7)                          Server (Py 3.11+)
────────────────                      ──────────────────────────────────
wake_listener.py                      agents/
  │                                     router.py         (triage)
  │  wake phrase + optional hint        chat.py           (chat specialist)
  ▼                                     chatbot.py        (Morgan CS RAG)
conversation.py  ────HTTP POST────▶    therapist.py      (empathetic; hands off)
  record → upload → parse → act        cbt_coach.py      (thought records)
  │                                     grounding_coach.py(grounding exercises)
  ▼                                     skills.py         (utilities)
naoqi services                        
  TTS / Motion / LEDs / Faces         tools/
                                        nao_actions.py    (18 robot action tools)
                                        pinecone_search.py
                                        emotion.py        (observe/log/distort/reframe)
                                        skills_tools.py   (time/weather/timer/todo)
                                        session_tools.py  (recap/load_recaps)

                                      server.py (Flask) — single POST /turn
                                      session.py — SQLiteSession + username migration
                                      config.py
```

### Agent graph

```
                ┌─────────┐
user hint → ───▶│ router  │────┬───▶ chat
                └─────────┘    ├───▶ chatbot
                               ├───▶ therapist ───▶ cbt_coach
                               │                 ├── grounding_coach
                               │                 └── (hand back)
                               └───▶ skills
```

Router reads the first user message and the optional wake-phrase hint, then hands off. Specialists can hand back to router or to each other (therapist → chatbot works naturally: "actually what's CS 341 like?").

### Session + memory

- **OpenAI Agents SDK `SQLiteSession(user_id, db="nao.db")`** handles the full conversation history across handoffs.
- **Thin `session.py` wrapper** adds:
  - `get_or_create_session(username)` — resolves "guest" vs real name
  - `migrate_username(old, new)` — when face recognition maps "guest" → "alice" mid-conversation
  - `load_recent_recaps(username, n=3)` — pulls last 3 therapist session summaries for context injection into the therapist agent's system prompt
- Memory file `memory.json` is deleted; SQLite replaces it at `server/nao.db`.

### Multimodal emotion detection

- NAO snaps a 640x480 JPEG at VAD onset via `utils/camera_capture.snap_quick()` (~50ms, async from audio recording).
- Multipart POST to `/turn` now includes `image` alongside `audio` (optional — not every turn needs it).
- Server builds the user message as multimodal content:
  ```python
  [{"type": "text", "text": transcript},
   {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]
  ```
- Therapist agent's system prompt: "You can see the user's face. Factor visible emotion into your response. Mention what you see only when relevant — don't narrate."
- Dedicated tool `observe_face() -> {dominant_emotion, secondary, notes}` for CBT/grounding sub-agents that want a structured read mid-exercise. The tool reads the most recent image attached to the current turn's user message. If none was sent, it returns `{error: "no_image"}` — the agent can then ask the user to face the camera next turn.

### Therapist agent deepened

**Main agent (`therapist.py`)** — empathetic, active-listening first, technique second. Tools:

| Tool | Purpose |
|---|---|
| `log_emotion(mood, intensity_1_10, trigger)` | Per-turn structured emotion log; feeds session recap. Replaces keyword-based `detect_mood_from_speech`. |
| `observe_face()` | Returns vision-derived face emotion read. |
| `identify_distortion(thought)` | Classifies one of 10 classic CBT distortions (catastrophizing, all-or-nothing, mind reading, personalization, fortune-telling, emotional reasoning, shoulds, labeling, magnification/minimization, filtering) + gentle explanation. |
| `suggest_reframe(thought, distortion)` | Returns 2 alternative balanced thoughts. |
| `set_led_color(color)` | NAO action tool — mood-reflecting LEDs. |
| `recap_session()` | End-of-session summary of emotions, thoughts, reframes; persists to user's therapist history. |

**`cbt_coach` sub-agent** — walks a **thought record**:

1. "Tell me what happened."
2. "What went through your mind?"
3. "How did that feel, 1–10?"
4. "Evidence for / evidence against that thought?"
5. "What's a more balanced way to see it?"

Uses `identify_distortion` + `suggest_reframe`. Hands back to `therapist` when done.

**`grounding_coach` sub-agent** — picks one of:

- **5-4-3-2-1 senses** — name 5 see, 4 hear, 3 feel, 2 smell, 1 taste
- **Box breathing** — 4s in, 4s hold, 4s out, 4s hold, 3 rounds
- **Body scan** — head-to-toe attention sweep

Selects based on user cue (panic → breathing; dissociation → 5-4-3-2-1). Hands back.

**Cross-session memory:** therapist system prompt includes last 3 session recaps so NAO can reference prior conversations ("last time we talked about your advisor meeting — how did it go?").

### NAO-side consolidation (`conversation.py`)

One loop replaces all 4 mode files:

```python
def run(initial_hint=None):
    username = resolve_user_via_face()   # utils.face_naoqi + ask_name if unknown
    greeting_done = False
    while True:
        wav = audio_handler.record_audio(...)
        if not wav: continue
        img = camera_capture.snap_quick()          # optional
        resp = post_turn(wav, img, username, initial_hint)
        initial_hint = None                         # only on first turn
        if resp.get("crisis"): handle_crisis(resp); continue
        speak(resp["reply"])
        for action in resp.get("actions", []):
            nao_execute.run(action)                 # dispatches {name, args}
        if exit_detection.detect(resp["user_input"]): break
```

`nao_execute.py` is a small dispatcher mapping tool names (`wave_hand`, `nod_head`, `change_eye_color`, `dance`, `spin`, `set_timer`, etc.) to naoqi calls.

### Request / response shape

**POST `/turn`** (multipart):

| Field | Type | Required | Purpose |
|---|---|---|---|
| `audio` | WAV file | yes | Whisper input |
| `image` | JPEG file | no | Vision input (therapist always sends) |
| `username` | string | yes | Session key |
| `hint` | string | no | `chat` \| `morgan` \| `therapy` \| `skills` |

**Response JSON:**

```json
{
  "username": "alice",
  "user_input": "I've been really stressed about finals",
  "reply": "That sounds heavy — what's the loudest thought right now?",
  "active_agent": "therapist",
  "actions": [
    {"name": "change_eye_color", "args": {"color": "blue"}}
  ],
  "crisis": false
}
```

`actions` is an ordered list — agent can wave *and* change LED in one turn. NAO executes them in order.

### Safety

- **Pre-dispatch `crisis_check`** (in `server.py`, not an agent tool) runs on the transcript before the agent receives the user message, every turn, every agent. Combines a fast keyword list (hard-fail on any hit) with a small LLM classification (`gpt-4o-mini`) for ambiguous phrasing. Positive → hardcoded response containing the 988 Suicide & Crisis Lifeline, encourages reaching out to a human, skips agent entirely, logs the event. Agent-facing `crisis_check` is intentionally absent — the check is a gate the agent cannot override.
- Therapist opening line: "I'll use my camera to check in on how you're feeling — is that okay? Say 'no camera' if you'd rather I didn't." If user declines, image is not sent for the rest of the session.
- System prompts: "Never diagnose. Never claim to be a therapist. Always recommend professional help for serious or ongoing distress."
- No medication advice, ever.

### Tracing

Enable OpenAI Agents SDK tracing via `OPENAI_AGENTS_TRACE=1` env var. Free visibility into handoffs, tool calls, latency at `platform.openai.com/traces`. Useful for debugging "why did the router not hand off to therapist?"

## File Layout

### New / kept

```
server/
  server.py                 Flask app, /turn + /health
  config.py                 Env config
  session.py                SQLiteSession + username migration
  agents/
    __init__.py
    router.py
    chat.py
    chatbot.py
    therapist.py
    cbt_coach.py
    grounding_coach.py
    skills.py
  tools/
    nao_actions.py          18 robot action tool definitions
    pinecone_search.py
    emotion.py
    skills_tools.py
    session_tools.py
  requirements.txt

main.py                     NAO entry — wake + conversation.run()
wake_listener.py            + optional hint extraction
conversation.py             NEW — single mode loop
audio_handler.py            unchanged
processing_announcer.py     unchanged
config.py                   unchanged
utils/
  camera_capture.py         + snap_quick()
  face_naoqi.py             unchanged
  ask_name_utils.py         unchanged
  exit_detection.py         unchanged
  name_utils.py             unchanged
  speech.py                 unchanged
  nao_execute.py            NEW — action dispatcher
```

### Deleted

- `chat_mode.py`
- `chatbot_mode.py`
- `therapist_mode.py`
- `mini_nao.py`
- `gpt_handler.py`
- `memory_manager.py`
- `face_store.py`
- `utils/face_utils.py`
- `utils/with_announcer.py`
- `utils/file_utils.py` (inlined where used)
- `memory.json`

### Net change

Roughly **~2600 → ~1200 lines**. Half the codebase gone; features added.

## Data Flow (turn-by-turn)

1. User says wake phrase. Optional mode phrase hints initial agent.
2. NAO `conversation.py` records audio via VAD; snaps JPEG at onset.
3. NAO POSTs `/turn` with `audio`, `image`, `username`, `hint`.
4. Server validates audio, transcribes with Whisper, runs `crisis_check(transcript)`.
5. If crisis: hardcoded safe response returned; agent skipped.
6. Else: build multimodal message, resolve session, pick initial agent (hint → direct, else router), run Agents SDK `Runner.run()` with session.
7. Agents SDK handles handoffs and tool calls internally. Final output is natural-language reply. Tool calls that match NAO action tools are collected into `actions[]` rather than executed server-side.
8. Server returns JSON.
9. NAO speaks reply, executes actions in order, loops.
10. [[Exit Intent Detection|Exit intent]] detected → break back to wake listener.

## Error Handling

| Failure | Response |
|---|---|
| Audio validation fails (short/corrupt WAV) | 503, NAO prompts user to try again |
| Whisper retry exhausted | 502, NAO says "I didn't catch that" |
| OpenAI API down / timeout | 503, NAO says "my brain's not responding, let's try again" |
| Pinecone query fails | Chatbot agent falls back to GPT-only, notes in trace |
| Vision image missing or corrupt | Server drops image silently, text-only |
| Tool call throws (pinecone_search, observe_face) | Agent is given the error as tool result; continues conversation |
| Unknown action name on NAO | Logged, skipped, other actions continue |
| Crisis check LLM unavailable | Fall back to keyword-only check; fail-safe (flag on any match) |

## Testing

- **Unit**: each tool callable directly with mocked inputs.
- **Agent-level**: use Agents SDK's `Runner` in tests — drive conversations programmatically, assert handoffs fire, assert tools called.
- **Integration**: Flask test client hits `/turn` with sample WAV+JPEG fixtures; assert response shape + agent routing.
- **Manual on-robot**: smoke-test each wake hint + each sub-agent flow.

## Configuration

Env vars (all with defaults in `config.py`):

| Var | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — (required) | OpenAI auth |
| `PINECONE_API_KEY` | — (required for chatbot) | Pinecone auth |
| `PINECONE_INDEX_NAME` | `msu-cs-knowledge` | |
| `PINECONE_NAMESPACE` | `docs` | |
| `NAO_IP` | `172.20.95.111` | |
| `NAO_PORT` | `9559` | |
| `SERVER_IP` | `172.20.95.105` | |
| `SESSION_DB` | `server/nao.db` | SQLiteSession file |
| `OPENAI_AGENTS_TRACE` | `1` | Enable built-in tracing |
| `ROUTER_MODEL` | `gpt-4o-mini` | |
| `THERAPIST_MODEL` | `gpt-4o` | Needed for vision; mini is text-only |
| `WHISPER_MODEL` | `whisper-1` | |

## Rollout

1. Branch: `refactor/agents-sdk` off `refactor/openai-upgrade-and-cleanup`.
2. Build server-side first. Can be tested with `curl` against `/turn` independently of NAO.
3. Build new `conversation.py` on NAO side; test against dev server.
4. Cut over in one commit: delete old mode files + ship new ones.
5. Keep `refactor/openai-upgrade-and-cleanup` tagged as rollback anchor.
6. Update Obsidian vault (`~/Documents/Obsidian Vault/Nao-OpenAI-Morgan-Assist/wiki/`) in a follow-up commit after implementation lands.

## Open Questions

None — all major design decisions answered during brainstorming.

## Related

- Obsidian vault: `~/Documents/Obsidian Vault/Nao-OpenAI-Morgan-Assist/wiki/` (current state of codebase, pre-refactor)
- Prior refactor: commit `7ff21dd` (OpenAI SDK v1 upgrade + shared utils extraction)
