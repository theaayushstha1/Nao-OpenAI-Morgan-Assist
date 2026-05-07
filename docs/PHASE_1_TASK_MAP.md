<!--
title: Phase 1 — Task Map & Contracts
tags: [phase-1, task-map, transport, websocket, fastapi, observability]
related: [DECISIONS, PRD_v2, spike_results, PHASE_2_TASK_MAP]
phase: "1"
status: shipped
-->

# Phase 1 — Task Map & Contracts

> **For all parallel agents working Phase 1.** Read this BEFORE writing any code. The PRD is at `docs/PRD_v2.md`. This file defines the **shared contracts** — frame envelopes, file ownership, env vars, log shape — so 9 agents can fan out without stepping on each other.

## Branch policy

- Every agent works in its own git worktree off `dev/architecture-rework`.
- Branch name: `dev/architecture-rework/phase-1/<slug>`.
- Final commit message format: `[Phase 1] <slug>: <one-line summary>`.
- Do **NOT** modify `requirements.txt` or `.env.example` directly. **Declare new deps in your commit message and final report.** A consolidator merges all deps in one commit after the parallel wave finishes.

## Worktree-level file ownership (hard rule)

| Slug | Files this agent OWNS — no one else writes them |
|------|-------------------------------------------------|
| `fastapi-app` | `server/app_ws.py`, `server/__init__.py` (only the new exports) |
| `tts-chunker` | `server/streaming.py` (extends in place; preserves any existing public symbols) |
| `observability` | `server/logging_setup.py`, `server/metrics.py` |
| `nao-audio-module` | `nao/audio_module.py`, `docs/spike_results.md` (write the spike findings here) |
| `nao-ws-client` | `nao/ws_client.py` |
| `nao-stream-tts` | `nao/stream_tts.py` (rewrite in place) |
| `nao-logger-main` | `nao/logger.py`, `nao/main.py` (rewire entry only) |
| `runner-config` | `run.sh`, `server/config.py` (extend ENV var list only) |
| `tests` | `server/tests/test_ws_smoke.py`, `server/tests/test_echo_regression.py`, `server/tests/conftest.py` (extend if needed) |

If you need to read a file owned by another agent, **read the contract below — do not read their in-flight code**.

---

## Frame envelope contract — both client and server MUST implement exactly this

WebSocket endpoint: `WS /ws/{username}` (FastAPI). Frames are JSON text. Audio payloads are base64 PCM/MP3 (binary frames are NOT used in Phase 1 — keep it text for debuggability; binary is a future opt-in).

### Client → Server

```jsonc
// One audio chunk, 20 ms PCM16 mono @ 16 kHz, base64-encoded
{
  "type": "audio_chunk",
  "seq": 42,                    // monotonic per session
  "ts_ms": 1714956000123.4,     // robot wall clock
  "data": "<base64 PCM>"
}

// One JPEG (camera per turn — image of the user)
{
  "type": "image",
  "seq": 5,
  "format": "jpeg",
  "data": "<base64 JPEG>"
}

// Control / metadata frames
{
  "type": "control",
  "subtype": "wake_event" | "end_of_utterance" | "barge_in"
           | "mic_resumed" | "session_open" | "session_close",
  "data": { /* subtype-specific */ }
}
```

`session_open` payload:
```jsonc
{ "face_id": "abc123", "brain_version": 2, "hint": "chat" | "therapy" | "skills" | null }
```

`wake_event` payload:
```jsonc
{ "face_id": "abc123", "gate": "mutual_gaze" | "proximity" | "speech" | "keyword",
  "confidence": 0.62, "distance_m": 0.9 }
```

`end_of_utterance` payload:
```jsonc
{ "robot_eou_hint": true, "energy_floor": 240, "trail_ms": 320 }
```

### Server → Client

```jsonc
// One TTS audio chunk for one sentence
{
  "type": "audio_chunk",
  "seq": 7,
  "format": "mp3",
  "text": "Sure, I can do that.",   // for logging / barge-in echo guard
  "data": "<base64 MP3>"
}

// One body action to dispatch via nao_execute
{
  "type": "action",
  "name": "wave_hand",
  "args": { "hand": "right" }
}

// Server-side controls
{
  "type": "control",
  "subtype": "tts_started" | "tts_ended" | "session_end" | "crisis_lock"
           | "transcript" | "agent_handoff",
  "data": { /* subtype-specific */ }
}
```

`transcript` is emitted once per turn after STT for client-side logging:
```jsonc
{ "transcript": "what classes does morgan offer", "stt_ms": 184 }
```

---

## Endpoint surface (FastAPI)

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/health` | Liveness — return `{"ok": true, "version": "phase-1"}` |
| `GET`  | `/metrics` | Prometheus exposition format (used by observability) |
| `WS`   | `/ws/{username}` | The voice loop |

---

## Env vars (all owned by `runner-config`)

| Var | Default | Purpose |
|-----|---------|---------|
| `USE_WS` | `0` | Feature flag — when `1`, run.sh starts uvicorn instead of Flask |
| `WS_HOST` | `0.0.0.0` | uvicorn bind |
| `WS_PORT` | `5050` | uvicorn port |
| `LOG_FORMAT` | `json` | structlog format, `json` or `console` |
| `LOG_LEVEL` | `INFO` | logging threshold |
| `METRICS_ENABLED` | `1` | toggle Prometheus exporter |
| `TTS_CHUNK_MIN_CHARS` | `30` | min chars before sentence chunker emits |
| `TTS_CHUNK_TIMEOUT_MS` | `400` | flush partial chunk if model pauses |
| `MIC_GATE_GRACE_MS` | `200` | mic resubscribe delay after last TTS chunk |
| `WS_RECONNECT_BACKOFF_MS` | `300,600,1200,2400` | robot reconnect schedule |

---

## Latency phases — exact label names (used by `observability` and `fastapi-app`)

`metrics.latency_ms` is a Prometheus Histogram, label `phase`, with buckets `[50, 100, 200, 400, 800, 1500, 3000, 8000]` ms. The labels you may use are exactly these — no others, no typos:

```
vad
stt
crisis_check
motion_trigger
agent_first_token
agent_complete
tts_synth_first_chunk
tts_synth_total
action_dispatch
e2e_user_to_first_audio
e2e_user_to_complete
```

---

## Logging shape (structlog, JSON)

Every per-turn log event uses these keys at the root:

```jsonc
{
  "ts": "2026-05-06T20:00:00.123Z",
  "level": "info",
  "event": "turn_complete" | "turn_error" | "wake_event" | "crisis_block" | "motion_match",
  "user": "<username>",
  "session_id": "<uuid>",
  "turn_idx": 7,
  "phase_ms": {
     "vad": 12, "stt": 184, "agent_first_token": 380,
     "tts_synth_first_chunk": 220, "e2e_user_to_first_audio": 712
  },
  "tool_calls": [{"name": "cs_navigator_search", "ms": 312}],
  "transcript": "...",
  "reply_preview": "first 80 chars",
  "outcome": "ok" | "rejected" | "crisis" | "motion_short_circuit"
}
```

Robot-side log: `~/nao_assist/logs/nao_<YYYY-MM-DD>.jsonl`, rotated at 50 MB, 5 backups.

---

## Reused-as-is modules (do NOT modify)

The new transport stitches together these existing modules unchanged. If you find a real bug, file it in your final report; do not silently patch.

- `server/safety.py:crisis_check` — call it on every transcript before agent dispatch
- `server/motion_trigger.py:detect` — short-circuit pattern matcher
- `server/openai_tts.py:synthesize` — already returns gain-amped MP3 bytes
- `server/vad_silero.py` — server-side authoritative voice gate
- `server/agents/*` — agent graph stays identical for Phase 1 (reuse `_run_agent` from `server/server.py` wholesale via import)

---

## Things explicitly OUT of Phase 1 scope

- Wake state machine (Phase 3)
- CS Navigator integration (Phase 5)
- Camera consent default ON (Phase 6)
- Robot brain.json (Phase 7)
- New onboarding (Phase 8)

If your code touches any of those areas, **stop and put it on the roadmap** — don't expand scope.

---

## Definition of done (per agent)

1. All files in your ownership row exist with non-stub implementations.
2. `python -m py_compile <each .py>` succeeds in your worktree (server-side files).
3. For robot-side files, structurally valid Python 2.7 syntax — no f-strings, no type hints, `from __future__ import print_function` at top.
4. Final commit message matches `[Phase 1] <slug>: <summary>`.
5. Final report (returned to dispatcher) lists: files written, LOC, declared dependencies, any contract questions for follow-up.
