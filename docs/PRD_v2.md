<!--
title: PRD v2 — From Vessel to Brain
tags: [prd, architecture, planning, sage-cbt]
related: [DECISIONS, spike_results, PHASE_1_TASK_MAP, PHASE_3_TASK_MAP, PHASE_4_TASK_MAP, PHASE_6_TASK_MAP, PHASE_9_TASK_MAP]
status: shipped
-->

# PRD: NAO Morgan Assist v2 — From Vessel to Brain

> Branch: `dev/architecture-rework` (off `main` @ `f606534`)
> Author: Aayush Shrestha · Drafted: 2026-05-06
> Status: **Plan ready for approval. No code changes yet.**

---

## Context

`main` ships a working NAO voice assistant: Flask server, OpenAI Agents SDK graph, energy-VAD, OpenAI TTS, motion-trigger shortcut, therapy + crisis gate. After listening to a 60-min NotebookLM walkthrough of the system, the operator (you) identified architectural smells that are not worth patching turn-by-turn — they need a coordinated rework.

**Vision shift:** today the robot is a **vessel** (microphone + speaker + motor controller; brain in cloud). Target state is a **brain** — robot owns identity, prompts, knowledge cache, presence detection, embodiment; the cloud is its memory + LLM, not its skull.

**Top complaints driving this rework:**

1. Flask is the wrong primitive for streaming voice — sync WSGI, no barge-in, no streaming TTS, walkie-talkie not phone-call.
2. Wake word feels amateurish ("hey nao chat mode"). Real robots (Furhat, Moxie, Astro) wake by face / proximity / gaze.
3. VAD cuts users off, drops on quiet, false-triggers in noise. 10 s hard cap is arbitrary.
4. Echo bleed — robot hears itself when no one is talking; sometimes converses with its own output.
5. Pinecone is overkill — operator already has a deployed Morgan-CS API on Cloud Run (`cs-chatbot`).
6. Therapist asks for camera consent, then sends a still image to a model that fails. Want default-on + better vision model.
7. Motors are barely used. 25 actuators + sound-source localization sit idle.
8. `user_cache.json` pattern works — extend it. Put a real local "brain" on the robot.
9. Onboarding is clunky. Want professional, minimal, research-backed.

**Out of scope (explicitly):** Replace working pieces — crisis gate, CBT/grounding sub-agents, ffmpeg amplification, Python 2.7 on robot — these stay.

---

## Decisions Made (and why)

| # | Decision | Why |
|---|----------|-----|
| D1 | **Transport: FastAPI + WebSocket** (Realtime API kept as parallel benchmark, not dismissed) | Python 2.7 client cannot speak WebRTC (aiortc requires py3.6+). Realtime API is ~50 ms faster baseline but corrupts audio under interruption, locks model, costs 3-5× per minute, and its multi-agent handoff is fragile. FastAPI + WS keeps multi-agent control, ~750 ms achievable e2e, full debuggability. **Phase 0.5 spike will benchmark both with the same test script before final commitment.** (See Research §1.) |
| D2 | **Wake: Hybrid face-first, word fallback** | ALFaceDetection always-on @ 30 fps; on known face within 0.3-1.5 m and ±60° → engage. "Hey NAO" still works as fallback for lighting failures. (HRI research §2.) |
| D3 | **STT location: Cloud, local VAD** | NAO V6's 1.4 GHz Atom can't run Whisper without 200-500 ms decoding penalty + accuracy loss. Send PCM/Opus over WS, server runs `gpt-4o-mini-transcribe` or Deepgram. Robot CPU stays free for face/motion. |
| D4 | **Motor utilization: Active embodiment** | Per-turn body-language tools driven by agent output; sound-source localization turns the head; passive idle breathing. Use the 25 motors as part of the conversation. |
| D5 | **Morgan knowledge: replace Pinecone with CS Navigator API** | `/Users/theaayushstha/Projects/cs chatbot/cs-chatbot` already has `POST /chat/stream` (auth) and `POST /chat/guest` (no-auth) deployed on Cloud Run. We delete Pinecone, add a thin tool that calls this endpoint. |
| D6 | **Camera consent: default ON** | Privacy-by-default ask was making vision unusable. Move consent to *opt-out* with explicit toggle in skills mode. |
| D7 | **Robot-side knowledge cache** | Extend `user_cache.py` pattern → `knowledge_cache.json` on robot for personal context, last-seen, preferences, system prompt fragments. Robot becomes stateful even when offline. |

---

## Research synthesis (one paragraph each)

**Transport benchmark.** Realtime API claims 300-500 ms client-to-audio; real-world p50 ~750 ms with tools, p95 >1 s. FastAPI + Whisper/Deepgram → GPT-4o → OpenAI TTS hits ~750 ms with streaming, scales further with parallelism. Realtime API has known audio-desync under interruption ([Latent Space, Apr 2025](https://www.latent.space/p/realtime-api)); model is locked to `gpt-realtime` snapshot; 15-min session ceiling. **Decision: FastAPI WS.**

**Wake & onboarding (HRI).** Furhat's continuous-perception model and Moxie's always-listening + silence-based turn-taking outperform wake-word UX in user studies. ALFaceDetection at 30 fps with confidence ≥ 0.35 catches faces at 0.3-2 m / ±60°. LED state machine (gray → blue → cyan → yellow) signals IDLE/DETECTED/LISTENING/SPEAKING. Median natural human turn-gap is 200 ms; >700 ms feels unnatural. Recommended: passive detect → soft chime → wait 3 s for speech → fall back to one-line greeting.

**Code audit.** Current `/turn` and `/stream_turn` flows are well-layered (auth → VAD → STT → hallucination filters → crisis → motion-trigger → agent → SSE TTS). `vad_silero.py` *is* wired (server-side, lazy-loaded line 159). `realtime_proxy.py` exists but is optional. NAO-side `audio_handler.py` is blocking-synchronous; mic is closed before TTS but no AEC. SQLite schema has 9 tables (users, sessions, user_prefs, recaps, weekly_themes, monthly_personas, safety_events, topology_trace, agent_sessions/messages). 97 tests pass; `stream_tts` and `audio_handler` are not unit-tested.

---

## Goals & Non-Goals

**Goals**
- End-to-end voice latency target: **< 800 ms p50, < 1.2 s p95** (mouth-close to first audio chunk). **This is aggressive — only achievable with streaming STT + streaming LLM tokens + parallel sentence-level TTS. Per-phase latency is tracked from Phase 1 day one (not retrofitted in Phase 9).**
- Barge-in support — user interrupts robot mid-sentence, robot stops within 200 ms.
- Wake without saying anything — robot greets known faces.
- Robot continues to function in degraded modes (no network → still does motion + greeting from local prompts).
- All Morgan CS questions answered by CS Navigator API, not Pinecone.
- Therapist sees the user every turn (default-on camera).
- Tests: every regex/parser module has unit tests; one e2e smoke covering wake → POST → response.

**Non-Goals (this rework)**
- Replacing OpenAI as LLM provider.
- Migrating off naoqi / Python 2.7 on the robot.
- Adding multi-language support.
- Replacing CBT/grounding sub-agents.
- On-robot Whisper/Vosk (deferred to a stretch phase).

---

## Phased Roadmap

> Each phase is sized to fit one focused execution session. They can be done in order; phases marked *parallel-safe* don't depend on the immediately preceding one.

### Phase 0 — Foundations (DONE)

- [x] `dev/architecture-rework` branch off `main` @ `f606534`
- [x] Transport benchmark research
- [x] HRI/onboarding research
- [x] Code audit map of current state
- [x] CS Navigator endpoint scan (`POST /chat/stream`, `/chat/guest`)

### Phase 0.5 — Transport + Mic-Streaming Spike (1 day, throwaway code)

**Why this exists:** the PRD assumes we can stream live mic audio from NAO over WebSocket to a server. That's a load-bearing assumption. `ALAudioRecorder` is **file-based** (writes WAV, hands the path back) — it is not a live stream. True live PCM requires a **NAOqi ALModule that subscribes to ALAudioDevice frames** (the `subscribe()` API delivering raw 16 kHz PCM buffers). Verifying this — plus measuring real Realtime-API performance on this exact hardware/network — must happen before we commit a week to Phase 1. Throwaway code only.

**Spike A — Live mic streaming over WebSocket**
- Write a minimal `ALModule` subclass on the robot that subscribes to ALAudioDevice frames and pushes 20 ms PCM chunks over a websocket-client 0.59.0 socket to a tiny FastAPI WS server running locally.
- Server echoes audio back via `ALAudioPlayer.playFile()` after writing to disk.
- Measure: chunk delivery latency, jitter, dropped frames over 60 s of speech.

**Spike B — Realtime API parallel benchmark**
- Same robot mic module, but route audio to OpenAI Realtime API over WebSocket (no WebRTC since py2.7 can't).
- Use the same test phrases and the same network. Capture: end-of-speech to first audio chunk, tool-call latency, audio glitches under interruption.

**Spike C — Mic-gate-during-TTS validation**
- Test the actual mic gate primitive (Spike A's ALAudioDevice path can `unsubscribe()` cleanly while TTS plays). Confirm we can stop ingesting frames during TTS playback in <50 ms, resume in <50 ms.

**Decision criteria** (one-pager output to `docs/spike_results.md`):
- If FastAPI WS p50 within 1.3× of Realtime API → commit to FastAPI WS (D1 confirmed).
- If FastAPI WS is 1.5×+ slower with no clear fix → reconsider hybrid (Realtime API for voice path, FastAPI WS for tools/agents).
- If `ALAudioDevice.subscribe()` ALModule path is unworkable on this NAO firmware → fall back to short-fragment file-based recording with overlap (worse latency, document it).

**Files (throwaway, deleted at end of spike)**
- `spike/nao_audio_module.py` — ALModule subscriber
- `spike/server_echo.py` — FastAPI echo
- `spike/server_realtime_proxy.py` — Realtime API proxy
- `docs/spike_results.md` — measurements + recommendation

**Verification**
- 100 utterances per path, latency histogram. p50 + p95 reported.
- Subjective audio quality A/B by operator.
- Final `docs/spike_results.md` committed; **no other code from this phase merges.**

### Phase 1 — Transport: Flask → FastAPI + WebSocket (with observability skeleton)

**Why first:** every other phase rides on the new transport. Switching last would cause double work. **Observability is built in from day one** (per feedback) — we cannot tune latency we don't measure, and retrofitting metrics after the rewrite means we ship blind.

**Server side**
- New `server/app_ws.py` (FastAPI). Endpoint `WS /ws/{username}`. Frame format: JSON envelope with `type` ∈ {`audio_chunk`, `image`, `tool_result`, `control`}.
- Port `safety.crisis_check`, `motion_trigger`, agent runner, `openai_tts.synthesize` into the WS handler — these modules are pure and reusable as-is.
- Streaming TTS — chunk LLM output by sentence (regex on `[.!?]\s`), synthesize each chunk in parallel, send as soon as audio is ready (don't wait for full reply).
- Replace SSE with binary WS frames for audio (smaller, no base64 overhead).
- Keep Flask `app.py` running side-by-side under `/legacy/*` for a deployment week, behind feature flag `USE_WS=1`.
- **Observability skeleton (built in, not retrofitted):**
  - `server/logging_setup.py` — `structlog` JSON logs from line one.
  - Per-turn timing block: `t_audio_in_first_chunk`, `t_audio_in_last_chunk`, `t_stt_complete`, `t_llm_first_token`, `t_tts_first_audio`, `t_tts_last_audio`, `t_action_dispatched`. Logged structured per turn.
  - `/metrics` Prometheus endpoint exposing `latency_ms_bucket{phase=...}` from turn 1.
  - Robot-side rotating JSONL log (`~/nao_assist/logs/`, 50 MB cap).

**Robot side**
- New `nao/ws_client.py` using `websocket-client==0.59.0` (last py2.7 release, already verified compatible).
- Background thread: WS receiver — handles incoming audio chunks via `ALAudioPlayer.playFile()`.
- **Live mic streaming via `ALAudioDevice.subscribe()` ALModule** (validated in Phase 0.5 Spike A). NOT `ALAudioRecorder` — that's file-based and won't stream. The ALModule pushes 20 ms PCM frames to the WS sender thread.
- **Mic gate during TTS** (corrected wording — `setOutputVolume(0)` mutes the **speaker**, not the mic):
  - **Primary:** call `ALAudioDevice.unsubscribe(<our module name>)` when TTS playback starts; resubscribe 200 ms after the last audio chunk completes.
  - **Secondary:** server-side echo window — drop any audio frames that arrive within `tts_active_window_ms` (catches in-flight buffers).
  - **Tertiary:** existing self-echo regex (already in `server.py`) as a third line of defense.
- Replace `nao/conversation.py` turn loop with a long-lived WS session.

**Files affected**

| File | Action |
|------|--------|
| `server/server.py` | Frozen on `main`, kept under `/legacy` shim |
| `server/app_ws.py` | NEW — FastAPI app |
| `server/streaming.py` | EXTEND — sentence chunker for TTS |
| `server/openai_tts.py` | REUSE as-is |
| `server/safety.py`, `server/motion_trigger.py` | REUSE as-is |
| `server/logging_setup.py` | NEW — structlog config (built in Phase 1) |
| `server/metrics.py` | NEW — Prometheus exporter (built in Phase 1) |
| `nao/conversation.py` | REPLACE with WS client loop |
| `nao/ws_client.py` | NEW |
| `nao/audio_module.py` | NEW — ALModule subscribing to ALAudioDevice frames |
| `nao/stream_tts.py` | TRIM — no SSE parsing, just chunk player |
| `nao/logger.py` | NEW — rotating JSONL log on robot |
| `run.sh` | Add `USE_WS=1` env, point at uvicorn |

**Verification**
- `pytest server/tests/test_ws_smoke.py` — synthetic client, 5 turns, asserts < 1 s p95.
- Manual: full demo conversation, measure p50/p95 with timestamps in `nao.log`.
- `/metrics` returns latency histogram with at least 5 phase buckets populated after a 5-turn conversation.
- Echo guard verification: stand robot 50 cm from a speaker playing prior reply at 70 dB during TTS playback; confirm zero turns triggered (combined effect of `unsubscribe` mic gate + server echo window + self-echo regex).

---

### Phase 2 — VAD + Echo Hardening

**Why:** Even on the new transport, brittle VAD will still cut users off. Echo bleed will still send the robot's own voice back to itself.

**VAD location decision (corrected from earlier draft):** Silero ONNX on a 1.4 GHz Atom running NAOqi Python 2.7 is high-risk — onnxruntime ARM/x86-32 builds for py2.7 are scarce and we'd burn days getting it stable. **Phase 2 keeps the robot's existing energy VAD** (with the calibration improvements below) and **leans on server-side Silero** (already wired in `server/vad_silero.py`) as the authoritative voice gate. On-robot Silero stays as a stretch goal post-Phase 9 if measurement shows the energy VAD is the bottleneck.

**Robot side (energy VAD improvements only)**
- Adaptive thresholds — rolling 30 s ambient floor; thresholds = `floor + dynamic_offset`. Replaces today's once-per-session calibration that drifts as classroom noise changes.
- **Remove the 10 s hard cap.** Replace with: end-of-utterance signal pushed to server, server makes the final call using its Silero output (since the robot can't run it). Allow up to 60 s of legitimate continuous speech.
- **Mic gate during TTS already covered in Phase 1** (ALAudioDevice unsubscribe). Phase 2 adds the **400 ms cooldown** after TTS ends before re-arming the energy gate (catches reverb).

**Server side (the heavy lifting)**
- Server-side Silero is the authoritative voice gate — it sees every PCM frame the robot streams.
- **Self-echo regex strengthening** — currently bigram-overlap > 0.6; raise to substring match against the actual TTS text that was just sent (we have it from the chunker).
- **End-of-utterance arbiter** — combine Silero confidence + energy hint from robot + optional semantic hint from `semantic_endpoint.py` (already wired). Tunable, observable via Phase 1 logging.

**Files affected**

| File | Action |
|------|--------|
| `nao/audio_handler.py` | REWRITE energy-VAD section, adaptive ambient floor |
| `nao/ws_client.py` | Send end-of-utterance hint frames + cooldown timer |
| `server/app_ws.py` | Cooldown + strengthen echo guard + EoU arbiter |
| `server/vad_silero.py` | PROMOTE to authoritative voice gate |
| `server/semantic_endpoint.py` | Optional EoU input |
| ~~`nao/silero_vad.py`~~ | **Deferred** — Silero on robot is a stretch goal post-Phase 9 |

**Verification**
- Test set: 30 recorded utterances (10 quiet room, 10 cafeteria, 10 with TTS playing nearby). Measure: false reject (cut-off) rate < 5%, false accept (echo / chatter triggers turn) rate < 2%.
- Stand robot 50 cm from a speaker playing the prior reply at 70 dB. Confirm zero turns triggered by echo.

---

### Phase 3 — Hybrid Wake: Face-First with Word Fallback

**Why:** UX delta is enormous. Switches the robot from "appliance with a wake word" to "presence-aware companion."

**Robot side**
- Continuous `ALFaceDetection` subscription at 30 fps (`subscribe("WakeFaceDetection", 100ms)`).
- Wake state machine in `nao/wake_state.py` — **face detection alone does NOT trigger speech.** Robot only speaks after a real engagement signal (sustained proximity, mutual gaze, or speech onset). This prevents random greeting of passersby.

```
IDLE       — eyes dim gray, downward gaze, no audio activity
             trigger: face confidence >= 0.35
             →
AWARE      — face detected, NOT YET ENGAGED
             eyes shift to soft blue (animacy cue, no chime, no speech)
             head tracks face gently (gaze toward, no greeting)
             evaluate engagement gates:
               • mutual gaze for >= 1.5 s, OR
               • distance drops below 1.0 m and stable for 1.0 s, OR
               • face conf >= 0.5 sustained 2 s with frontal angle (±30° yaw), OR
               • speech onset detected by VAD, OR
               • "hey nao" keyword heard
             if no gate fires within 8 s OR face lost → IDLE silently
             →
ENGAGED    — engagement gate fired
             soft chime (80 dB, 0.2 s), eyes brighten to solid blue
             open WS session, send wake_event with face_id + which gate fired
             →
LISTENING  — robot has greeted (server greeting based on face_id) OR user has spoken
             eyes cyan, 2.5 s gaze aversion cycle
             stream PCM
SPEAKING   — TTS playing
             eyes warm yellow, mouth animation if available
             mic gated (per Phase 1)
```

- Failure recovery — face lost mid-conversation: hold LISTENING for 5 s; if not restored, soft "I've lost sight of you" then IDLE.
- Multi-person rule — closest face within 1.5 m wins. Secondary face appearing during conversation is acknowledged with a head-tilt nod but not addressed.
- "Hey NAO" wake word retained via `ALSpeechRecognition` as fallback — hybrid mode (D2). Word fallback can also trigger ENGAGED directly without a face match (for occluded users / lighting failures).

**Server side**
- WS `control` message: `wake_event` → server logs to `safety_events` with face_id, distance, confidence.
- New session begins on `wake_event`; SQLiteSession resumes if same `face_id` was seen in last 24 h.

**Files affected**

| File | Action |
|------|--------|
| `nao/wake_listener.py` | KEEP for word fallback; gate behind hybrid mode |
| `nao/wake_state.py` | NEW — state machine + LED driver |
| `nao/utils/face_naoqi.py` | EXTEND — confidence + distance from ALFaceDetection raw output |
| `nao/leds.py` | NEW — color/intensity helpers |
| `nao/main.py` | REWRITE entry — boots wake_state, not direct conversation loop |
| `server/app_ws.py` | Handle `wake_event` control frames |

**Verification**
- 10 trials walking up to robot from 2 m and stopping in front of it → ENGAGED fires within 1.5 s of arrival (after engagement gate, not just detection).
- 10 trials walking past at 2 m perpendicular without stopping → **zero AWARE→ENGAGED transitions** (this is the main false-wake protection).
- 10 trials standing in robot's view but ignoring it (looking at phone, talking to a friend) → robot stays in AWARE silently for 8 s, then drops to IDLE.
- 10 trials with "hey nao" word from out-of-view → fallback fires ENGAGED within 600 ms.

---

### Phase 4 — Active Embodiment (the 25 motors)

**Why:** Operator wants the robot to *feel embodied*. Vessel → brain.

**Sound-source localization**
- Subscribe `ALSoundLocalization` (NAOqi-native, 4-mic array). On speech onset, head turns toward speaker within 300 ms (yaw + pitch).
- Cache last-known speaker direction; head returns there when robot speaks back.

**Per-turn body language synthesis**
- New tool `gesture(intent: str)` exposed to therapist + chat agents. Intents: `nod`, `shake`, `lean_in`, `lean_back`, `open_arms`, `point_self`, `point_listener`, `shrug`, `tilt_curious`, `breath_deep`.
- Agent calls `gesture(...)` *during* TTS playback (parallel action queue, not blocking).
- Therapist auto-emits `nod` on reflective phrases (regex match: "I hear you", "that makes sense", etc.).
- Chat agent uses `lean_in` on questions, `open_arms` on confirmations.

**Idle breathing & gaze**
- Background thread on robot — when in IDLE for >5 s, run gentle chest/shoulder breathing animation (already in `play_animation('breathing')`).
- When in LISTENING, gaze drifts to face every 2.5 s with ±8° head yaw.

**Files affected**

| File | Action |
|------|--------|
| `nao/utils/nao_execute.py` | EXTEND — add `gesture` dispatch |
| `nao/sound_localize.py` | NEW — ALSoundLocalization subscriber |
| `nao/idle_motion.py` | NEW — breathing + gaze loops |
| `server/tools/nao_actions.py` | EXTEND — add `gesture` tool |
| `server/agents/therapist.py` | UPDATE prompt — call gesture on reflection |
| `server/agents/chat.py` | UPDATE prompt — gesture during turn |

**Verification**
- 20 trials: speak from robot's left, robot's head turns left within 500 ms.
- Therapist session video review — assess gesture appropriateness (subjective; aim for "helpful, not distracting").

---

### Phase 5 — CS Navigator Integration

**Why:** Replaces Pinecone with the operator's existing, deployed Morgan-CS knowledge service.

**Server side**
- New tool `server/tools/cs_navigator.py` exposing `cs_navigator_search(query: str) -> str`.
- Posts to `https://<cs-navigator-cloud-run-url>/chat/stream` with `Authorization: Bearer <token>`.
- Token from `.env`: `CS_NAVIGATOR_URL`, `CS_NAVIGATOR_TOKEN`.
- Uses `/chat/guest` for unauthenticated demos; `/chat/stream` once we have the user token plumbing.
- Streaming response — relay tokens as they arrive (no buffering).

**Agent rewiring**
- `server/agents/chatbot.py` — replace Pinecone tool with `cs_navigator_search`. Keep system prompt; CS Navigator handles RAG internally.
- Delete `server/tools/pinecone_search.py` (after Phase 5 stable for 3 sessions).
- Drop `pinecone-client` from `requirements.txt`.

**Operator inputs needed** (already confirmed exists, awaits handoff)
- CS Navigator Cloud Run base URL
- API token / service account creds
- Confirmation that `/chat/stream` accepts our payload shape

**Files affected**

| File | Action |
|------|--------|
| `server/tools/cs_navigator.py` | NEW |
| `server/agents/chatbot.py` | REWIRE tool |
| `server/tools/pinecone_search.py` | DELETE (after stable) |
| `server/config.py` | ADD env vars |
| `requirements.txt` | REMOVE pinecone-client |
| `.env` template | ADD CS_NAVIGATOR_URL, CS_NAVIGATOR_TOKEN |

**Verification**
- 20 Morgan-specific questions side-by-side: old Pinecone vs new CS Navigator. Assess answer quality, relevance, latency.
- Confirm guest endpoint works without leaking PII.

---

### Phase 6 — Therapist Vision-On

**Why:** Operator confirmed in-session that camera is mostly broken — image is sent but observe_face says it can't see. Want default-on, working multimodal.

**Camera defaults**
- `user_prefs.camera_consent` default → `1` (on) for new users.
- Migration: existing users keep their setting.
- **Visible "camera active" cue (mandatory — privacy-by-default removed, so the cue replaces it as the trust mechanism):**
  - **LED:** dedicated **green dot on the right ear LED ring** while a frame is being captured (lights for ~150 ms per snap, ~2× per turn). Distinct from the wake state machine LEDs so it can't be confused with another state.
  - **Posture:** head subtly orients toward the user when capturing (already happens via Phase 4 sound localization; here we add a tiny pitch tilt so it's visibly "looking").
  - **First-turn announce:** on the very first turn of a session where camera_consent=1, robot says one short line: *"Heads up — my camera is on for this conversation. Say 'stop watching me' anytime."* Only first turn; not repeated.
- **"Stop watching me" command:**
  - **Pattern-trigger** added to `motion_trigger.py` (deterministic, doesn't need LLM): phrases `["stop watching me", "stop looking at me", "turn off the camera", "camera off", "no camera"]` → instantly disables camera for the rest of the session.
  - On trigger: green LED off permanently for the session, `IMAGE_PER_TURN=0` for this session, robot acknowledges *"Got it, camera off."*
  - Persists to `user_prefs.camera_consent=0` for next session unless user re-enables.
- **Re-enable command:** `["turn camera back on", "you can watch again", "camera on"]` → re-enable for session, prompt confirmation before persisting.
- New skill tool `disable_camera()` and `enable_camera()` exposed to skills agent as the LLM path (the pattern-trigger is the fast path).

**Vision pipeline**
- `IMAGE_PER_TURN=1` is already wired. Issue is the model call.
- `server/tools/emotion.py:observe_face` — debug the GPT-4o vision call. Likely the JPEG isn't being base64-encoded properly or the message format is wrong. Add log of full request payload size in dev mode.
- Upgrade to **latest available OpenAI multimodal model** at execution time (today: `gpt-4o`; if `gpt-5` is GA when Phase 6 lands, use it). Single config: `VISION_MODEL` env var.
- Therapist auto-calls `observe_face` on every turn (currently model-discretion). Promote to required first tool call when camera_consent = 1.

**Files affected**

| File | Action |
|------|--------|
| `server/tools/emotion.py` | DEBUG vision call; add error logging |
| `server/agents/therapist.py` | UPDATE prompt — always observe_face first |
| `server/config.py` | ADD `VISION_MODEL` |
| `server/session.py` | UPDATE default `camera_consent` to 1 |
| `server/migrations/` | NEW — migration script for new default |

**Verification**
- 10 therapist turns with camera on. Verify: every turn has an `observe_face` call in trace; output is non-empty; model didn't hallucinate "I don't see anyone."
- Affect changes (smile, frown) reflected in next turn's response.

---

### Phase 7 — Robot-Side Brain (Identity & Preferences Cache, **NOT a knowledge base**)

**Why:** Today the robot is dumb without network — wake works, but conversation is impossible. The `user_cache.json` proves the pattern; extend it. **Critical scoping (per feedback):** the robot's local brain holds **identity, preferences, and prompt fragments only**. It does NOT mirror the Morgan CS knowledge base — that stays in CS Navigator API where it belongs (single source of truth, server-side updates without redeploying robots). Trying to sync a knowledge base to the robot would create staleness, conflict resolution headaches, and disk pressure with no UX win.

**What lives on the robot (small, synced)**
- `~/nao_assist/brain.json` — capped at **64 KB total**. Schema:
  ```json
  {
    "version": 2,
    "users": {
      "<face_id>": {
        "display_name": "...",
        "last_seen_iso": "...",
        "session_count": 12,
        "preferences": {"likes": [...], "dislikes": [...], "favorite_color": "..."},
        "ongoing_topics": ["last 3 topic tags only"],
        "last_recap_summary": "≤300 char rolling summary"
      }
    },
    "system_prompt_fragments": {
      "robot_identity": "I'm NAO at Morgan State CS...",
      "session_greeting_template": "Welcome back, {name}.",
      "first_meeting_template": "Hi, I'm NAO. What's your name?"
    }
  }
  ```

**What does NOT live on the robot**
- Morgan CS knowledge → CS Navigator API (Phase 5).
- Full conversation transcripts → server SQLite.
- Per-turn emotion logs → server SQLite.
- Therapy session contents → server SQLite.

**Robot uses local cache for**
- Greeting personalization without server round-trip ("Welcome back, Aayush.")
- Identity in WS handshake — server doesn't re-derive who's at the door each session.
- **Limited offline mode:** if WS fails, robot can: acknowledge presence, greet by name, say "I can't reach my brain right now — try again in a moment." It does NOT attempt to answer questions offline.

**Sync mechanism**
- Server is authoritative. Robot brain.json is a derivative cache.
- WS handshake: robot sends `{face_id, brain_version}` → server responds with `{updates: {...}}` if newer state exists (last_seen, new preferences inferred from conversation, updated recap).
- Robot writes atomically (`brain.json.tmp` → rename) and validates schema on read; corrupt cache → wipe and re-sync from server.
- LRU eviction when approaching 64 KB cap — drop oldest unused user entries (keep top 10 by recency).

**Files affected**

| File | Action |
|------|--------|
| `nao/utils/brain.py` | NEW |
| `nao/utils/user_cache.py` | EXTEND or merge into brain.py |
| `nao/ws_client.py` | UPDATE handshake to include cache summary |
| `server/app_ws.py` | Handle cache sync messages |
| `server/session.py` | EXTEND — push updates back to robot |

**Verification**
- Pull network cable mid-conversation. Robot should: stop processing turns, but still respond to wake events with cached greeting.
- Restart robot. Verify cache rehydrates user identity + preferences + last topic.

---

### Phase 8 — Onboarding Polish

**Why:** Apply HRI research. Current "hey nao chat mode" is the worst UX in the system.

**New onboarding flow**
- First-time user (no face match): face detected → soft chime → 1 s pause → "Hi, I'm NAO. I haven't met you yet — what's your name?" → record → extract → confirm by repetition → learn face silently in background → "Got it, [name]. Pleasure to meet you."
- Returning user: face match → soft chime → "Welcome back, [name]." → wait for speech.
- Unrecognized speech (low confidence): "Sorry, I didn't catch that — could you say it again?" — never drop to IDLE during recovery.
- Group scenario: > 1 face within 1.5 m → "Hi everyone — who'd like to chat first?"

**Mode selection (replaces "say chat / therapy / skills")**
- Mode is **inferred** from first turn content via the existing router agent. No explicit mode keyword required.
- Optional power-user shortcut retained — saying "switch to therapy" mid-conversation triggers handoff.

**Files affected**

| File | Action |
|------|--------|
| `nao/conversation.py` (or its WS replacement) | REWORK onboarding sequence |
| `nao/utils/ask_name_utils.py` | UPDATE — single combined prompt |
| `server/agents/router.py` | KEEP — already content-routing |
| `nao/wake_state.py` | UPDATE — state-machine integration |

**Verification**
- 5 trials: new user, robot greets and learns face within 30 s, no manual mode keyword needed.
- 5 trials: returning user, robot greets by name within 800 ms of face detection.

---

### Phase 9 — Test Hardening + Dashboards (observability skeleton already shipped in Phase 1)

**Why:** Phase 1 shipped `structlog`, `/metrics`, and robot JSONL logs (per feedback — observability built in, not retrofitted). Phase 9 is about **upgrading from skeleton to production-grade**: dashboards on the metrics, broad unit/integration coverage, and regression snapshots.

**Dashboards (build on Phase 1 metrics)**
- Grafana board with panels: `latency_ms p50/p95` per phase (vad / stt / first_token / first_audio / last_audio / action_dispatch), tool-call frequency, wake-state transition counts, crisis-gate triggers, camera-off events.
- Alert: p95 latency > 1.5 s for 5 min → Slack webhook.
- Optional LangSmith tracing integration for the agent graph (already in test deps).

**Tests**
- Unit: `motion_trigger`, `name_utils`, `safety.crisis_check`, `wake_state` transitions, `brain.json` schema validation, camera "stop watching me" pattern.
- Integration: WS smoke test (extends the Phase 1 minimum into a 20-turn scenario), echo-bleed regression (speaker playing TTS during recording), face-wake gating (no false wake from passersby).
- Regression: snapshot tests on therapist responses for known transcripts (use `syrupy`, already in deps).
- Load: 5 concurrent WS sessions, confirm no per-user state crosstalk (currently risky given module-level dicts).

**Files affected**

| File | Action |
|------|--------|
| `server/dashboards/grafana_voice.json` | NEW — Grafana JSON |
| `server/tests/test_ws_smoke.py` | EXTEND from Phase 1 minimum |
| `server/tests/test_motion_trigger.py` | NEW |
| `server/tests/test_wake_state.py` | NEW |
| `server/tests/test_brain_cache.py` | NEW |
| `server/tests/test_echo_regression.py` | NEW |
| `server/tests/test_camera_consent.py` | NEW |
| `server/tests/test_concurrent_users.py` | NEW |

**Verification**
- `pytest -q` passes 100% on local + CI.
- Open Grafana / Prom UI and see latency histogram update during a live demo.

---

## Files Affected — Master Map

| Component | Phases | Files |
|-----------|--------|-------|
| Spike (throwaway) | 0.5 | `spike/nao_audio_module.py`, `spike/server_echo.py`, `spike/server_realtime_proxy.py`, `docs/spike_results.md` |
| Server transport | 1 | `server/app_ws.py` (NEW), `server/streaming.py`, `server/logging_setup.py` (NEW, Phase 1), `server/metrics.py` (NEW, Phase 1) |
| Server agents/tools | 4, 5, 6 | `server/agents/{therapist,chat,chatbot}.py`, `server/tools/{cs_navigator,emotion,nao_actions}.py` |
| Server safety/session | 6, 7 | `server/session.py`, `server/safety.py` (no change), `server/migrations/` |
| Robot transport | 1, 7 | `nao/ws_client.py` (NEW), `nao/audio_module.py` (NEW, ALAudioDevice subscriber), `nao/conversation.py` (REPLACE), `nao/stream_tts.py`, `nao/logger.py` (NEW, Phase 1) |
| Robot perception | 2, 3, 4 | `nao/audio_handler.py` (energy VAD only — Silero deferred), `nao/wake_state.py` (NEW), `nao/leds.py` (NEW), `nao/sound_localize.py` (NEW) |
| Robot embodiment | 4, 8 | `nao/idle_motion.py` (NEW), `nao/utils/face_naoqi.py`, `nao/utils/ask_name_utils.py` |
| Robot brain | 7 | `nao/utils/brain.py` (NEW, capped at 64 KB), `nao/utils/user_cache.py` |
| Camera consent | 6 | `server/motion_trigger.py` (extend with "stop watching me"), `server/tools/skills_tools.py` (enable/disable_camera) |
| Tests + dashboards | 9 | `server/tests/test_*` (multiple), `server/dashboards/grafana_voice.json` (NEW) |

---

## Reused Existing Utilities (don't rebuild)

| Module | Reuse for |
|--------|-----------|
| `server/safety.py:crisis_check` | Wraps every WS turn unchanged |
| `server/motion_trigger.py:detect` | Pre-agent shortcut on every WS turn unchanged |
| `server/openai_tts.py:synthesize` | TTS with ffmpeg amplification, used per sentence chunk |
| `server/vad_silero.py` | Server-side voice gate, sanity check |
| `server/agents/cbt_coach.py`, `grounding_coach.py` | Sub-agents of therapist, no changes |
| `server/agents/router.py` | Content-based routing, used in Phase 8 |
| `nao/utils/face_naoqi.py` | Extended (not replaced) for confidence/distance |
| `nao/utils/name_utils.py` | Reused unchanged in Phase 8 |
| `nao/utils/exit_detection.py` | Reused for end-of-conversation |
| `nao/utils/nao_execute.py` | Extended with `gesture` dispatch in Phase 4 |

---

## Verification — End-to-End

After all phases land, the demo script is:

1. Power on robot. Walk into the room.
2. Robot detects face within 800 ms → eyes turn blue → soft chime.
3. Speak: "Hi NAO." → robot greets by name (cached): "Welcome back, Aayush."
4. Speak: "What's the schedule for CS 491 next semester?" → router → chatbot agent → CS Navigator API → streaming TTS reply with first audio < 1 s.
5. Speak: "Show me a dance." → motion_trigger shortcut → robot dances.
6. Walk to the side of the robot mid-conversation → head turns to follow.
7. Say: "I'm feeling anxious about exams." → router → therapist → camera observes affect → empathic reflection with `nod` gesture → grounding_coach handoff if user asks.
8. Test crisis: (privately, off-demo) "I want to hurt myself." → 988 hotline reply, no LLM in loop.
9. Pull network cable → robot still acknowledges presence + greeting from cache.
10. End session: "Goodbye." → robot bows + idle breathing.

Latency target across the demo: p50 < 800 ms, p95 < 1.2 s.

---

## Decisions to Confirm at Execution Time

| Decision | When |
|----------|------|
| Vision model — `gpt-4o` now or wait for `gpt-5`? | Phase 6 |
| Local STT (whisper.cpp) as stretch goal? | Post-Phase 9 |
| Promote `mi_coach` from experimental → first-tier? | Phase 4-6 (depends on gesture work landing) |
| Multi-language support (Spanish, French)? | Future |
| OAuth on `/chat` vs guest token? | Phase 5 |

---

## Risks

| Risk | Mitigation |
|------|------------|
| Phase 0.5 spike reveals `ALAudioDevice.subscribe()` ALModule path doesn't work on this NAO firmware | Document and fall back to short-fragment file recording with overlap; latency target relaxes to < 1.2 s p50 |
| Phase 0.5 spike reveals FastAPI WS is meaningfully slower than Realtime API | Hybrid path: Realtime API for voice, FastAPI WS for tools/agents. Plan has decision criteria written in Phase 0.5. |
| `websocket-client 0.59.0` ages out, breaks on naoqi 2.8 | Pin in `requirements.txt`; have aiortc-via-bridge as a fallback if needed |
| Energy VAD remains brittle in noisy classrooms even with adaptive ambient | Server Silero is authoritative; semantic_endpoint can break ties; on-robot Silero unlocked as stretch goal post-Phase 9 |
| CS Navigator endpoint shape doesn't match our agent's expected RAG return | Adapter layer in `cs_navigator.py` — translate any shape |
| Sound-source localization is laggy on NAO V6 | Cap head movement to 30°/s; skip SSL for short turns < 2 s |
| Camera-default-on creates a privacy complaint at demo | Three-layer mitigation: visible green-LED-while-capturing cue, first-turn audible heads-up, instant "stop watching me" pattern-trigger |
| Wake state machine fires ENGAGED on every passerby | AWARE state requires a real engagement signal (mutual gaze ≥ 1.5 s, sustained proximity, speech, or keyword) before transitioning |
| Latency target < 800 ms p50 misses | Tracked from Phase 1 day one via `/metrics`; if missed, decision on stretch optimizations (streaming Whisper, smaller TTS chunks, edge model) at Phase 5 review |
| Brain cache scope creeps into knowledge | Hard 64 KB cap + schema validation; CS knowledge stays in CS Navigator (single source of truth) |
| WS connection drops mid-conversation | Auto-reconnect with exponential backoff; queue audio on robot during outage; resume from last `t_audio_in_first_chunk` |

---

## Things Explicitly Kept From `main` (do not change)

- `server/safety.py` crisis gate — perfect as-is
- `server/agents/cbt_coach.py`, `grounding_coach.py` — keep
- `server/openai_tts.py` ffmpeg +16 dB amplification — keep
- `server/motion_trigger.py` — keep (still useful as bypass)
- Python 2.7 on robot — non-negotiable, naoqi locked
- SQLite schema (9 tables) — extend, do not migrate

---

## Branch Strategy

- All phase work happens on `dev/architecture-rework` (current branch).
- Per phase, sub-branches: `dev/architecture-rework/phase-N-<name>` if a phase needs splitting.
- Merge into `dev/architecture-rework` after phase verification passes.
- Final merge into `main` only after Phase 9 + full e2e demo passes.
- `main` stays at `f606534` until then — that's the working baseline.
