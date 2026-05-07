---
title: Engineering Decisions
tags: [decisions, architecture, post-mortem, debugging]
related: [PRD_v2, spike_results, PHASE_1_TASK_MAP, PHASE_4_TASK_MAP]
status: living-document
last_updated: 2026-05-07
---

# Engineering Decisions

How key architectural and runtime problems were navigated. Each entry: **the problem**, **what we tried**, **what shipped**, **why**.

Cross‑references the [PRD](PRD_v2.md), [transport spike](spike_results.md), and the per‑phase task maps.

---

## Index

| # | Decision | Phase | Outcome |
|---|---|---|---|
| [D1](#d1-transport-fastapi--websocket-vs-realtime-api) | Transport: FastAPI + WS vs OpenAI Realtime API | [0.5](spike_results.md), [1](PHASE_1_TASK_MAP.md) | FastAPI + WS |
| [D2](#d2-stt-location-on-robot-vs-cloud) | STT location: on‑robot vs cloud | [1](PHASE_1_TASK_MAP.md) | Cloud (3 backends) |
| [D3](#d3-vad-silero-on-robot-vs-server) | VAD: Silero on robot vs server | [2](PHASE_2_TASK_MAP.md) | Server‑side authoritative |
| [D4](#d4-wake-face-first-with-keyword-fallback) | Wake: face‑first vs keyword | [3](PHASE_3_TASK_MAP.md) | Hybrid face‑first |
| [D5](#d5-knowledge-base-pinecone-vs-cs-navigator-api) | Knowledge: Pinecone vs CS Navigator | [5](PHASE_5_TASK_MAP.md) | CS Navigator (Cloud Run) |
| [D6](#d6-camera-consent-default-on-with-three-layer-privacy) | Camera consent: default on or off | [6](PHASE_6_TASK_MAP.md) | Default on, privacy LED + heads‑up + stop‑pattern |
| [D7](#d7-vision-cache-vs-fresh-per-question) | Vision: cache or fresh per question | 12 | Removed cache (fresh) |
| [D8](#d8-mic-lifecycle-tts-ended-vs-playback-drained) | Mic resume: server `tts_ended` vs local playback drained | 12 | Wait for local drain |
| [D9](#d9-action-dispatch-on-recv-thread-vs-worker) | Action dispatch on recv thread vs worker | 12 | Single worker queue |
| [D10](#d10-blocking-vs-non-blocking-behavior-calls) | `runBehavior` vs `startBehavior` | 12 | `startBehavior` everywhere |
| [D11](#d11-router-prompt-allowing-self-answer) | Router agent answering visual questions | 12 | Sensory grounding rule + handoff triggers |
| [D12](#d12-crisis-reply-tone) | Crisis hotline reply wording | 12 | Validated wording, drops "I'm glad you told me" |

---

## D1. Transport: FastAPI + WebSocket vs Realtime API

**Problem.** Voice latency target is < 800 ms p50 user‑close to first audio. Streaming is non‑negotiable. Two paths:

- OpenAI Realtime API: ~300–500 ms baseline, but model is locked, costs 3–5×, and corrupts audio under interruption ([Latent Space, Apr 2025](https://www.latent.space/p/realtime-api))
- FastAPI + WebSocket with our own STT/LLM/TTS pipeline

**What we tried.** [Phase 0.5 spike](spike_results.md) wrote a throwaway `ALModule` subscribing to `ALAudioDevice` frames and pushed 20 ms PCM chunks over a `websocket-client 0.59.0` socket to a tiny FastAPI WS server. Measured chunk delivery latency, jitter, dropped frames over 60 s of speech. Tested barge‑in on both paths.

**Shipped.** **FastAPI + WebSocket.** `server/app_ws.py` + `nao/ws_client.py`. WS p50 measured 1.2× Realtime baseline — within budget. Multi‑agent control, full debuggability, no model lock.

**Why not Realtime API.** Audio desync under interruption is a deal‑breaker for barge‑in UX. A 15‑min session ceiling and locked snapshot are unacceptable for a research platform.

---

## D2. STT location: on‑robot vs cloud

**Problem.** NAO V6's 1.4 GHz Atom can't run Whisper without 200–500 ms decoding penalty plus accuracy loss.

**Shipped.** Cloud STT via three swappable backends (`server/deepgram_asr.py`, `server/elevenlabs_stt.py`, OpenAI Whisper). Robot streams PCM/Opus over WS, server transcribes. CPU stays free for face/motion.

A/B tooling lives in [`sim/stt_ab.py`](../sim/stt_ab.py) — toggle `USE_DEEPGRAM` / `USE_ELEVENLABS_STT` in `.env`.

---

## D3. VAD: Silero on robot vs server

**Problem.** Energy‑VAD on the robot is brittle in noisy classrooms. Silero ONNX runtime for py2.7 ARM/x86‑32 is scarce and would burn days getting stable.

**Shipped.** **Server‑side Silero is authoritative.** Robot keeps its energy gate with adaptive ambient calibration (rolling 30 s floor) for the local speech‑onset signal; the server sees every PCM frame and makes the final EoU call. On‑robot Silero is a stretch goal post‑Phase 9.

See [`server/vad_silero.py`](../server/vad_silero.py) and [`server/semantic_endpoint.py`](../server/semantic_endpoint.py).

---

## D4. Wake: face‑first with keyword fallback

**Problem.** "Hey NAO chat mode" is the worst UX in the system. Real robots (Furhat, Moxie, Astro) wake by face/proximity/gaze.

**Shipped.** Hybrid wake state machine ([Phase 3](PHASE_3_TASK_MAP.md), `nao/wake_state.py`):

```
IDLE → AWARE (face detected) → ENGAGED (gate fires) → LISTENING → SPEAKING
```

Engagement gates: mutual gaze ≥ 1.5 s, sustained proximity, sound onset, or "hey NAO" keyword. **Face detection alone never triggers speech** — that's what stops the robot from greeting passersby. Verified with 10 walk‑past trials → zero false wakes.

---

## D5. Knowledge base: Pinecone vs CS Navigator API

**Problem.** Pinecone was overkill — operator already had `cs-chatbot` deployed on Cloud Run with `/chat/stream` (auth) and `/chat/guest` (no‑auth).

**Shipped.** [`server/tools/cs_navigator.py`](../server/tools/cs_navigator.py) — thin client that calls the existing Cloud Run service. Pinecone removed. Single source of truth, server‑side updates without redeploying the assistant.

---

## D6. Camera consent: default on with three‑layer privacy

**Problem.** Privacy‑by‑default ask was making vision unusable (camera consent prompts every session). But default‑on without disclosure is a privacy violation.

**Shipped.** [Phase 6](PHASE_6_TASK_MAP.md) three‑layer consent:

1. **Visible green ear‑LED** while a frame is being captured (~150 ms per snap)
2. **First‑turn audible heads‑up** — "Heads up, my camera is on for this conversation. Say 'stop watching me' anytime."
3. **Pattern‑trigger `stop watching me`** — instant pre‑LLM short‑circuit that disables camera for the session, persists to `user_prefs`

Verified at [`server/motion_trigger.py`](../server/motion_trigger.py).

---

## D7. Vision cache vs fresh per question

**Problem.** Initial design cached the GPT‑4o vision summary for 5 minutes per session. **Real bug observed in user testing**: a friend asked the same visual question minutes later in a different setting → NAO replied with the cached description of the **previous user**.

**Shipped.** Cache removed entirely. Every visual‑trigger phrase fires a fresh GPT‑4o call against the latest stashed image. Trade‑off: ~1.5 s per visual question. Acceptable because visual questions are infrequent (gated by trigger phrases, not every turn).

Commit: `a1842a3` "remove vision cache — every visual question runs fresh".

---

## D8. Mic lifecycle: server `tts_ended` vs local playback drained

**Problem.** Self‑echo loop where NAO transcribed its own TTS reply. Logs showed `FIRST PCM captured` appearing **before** both `blocking_play_done` lines — mic was open while speaker was still active.

**Root cause.** `tts_ended` from server only signals "server stopped sending audio". The robot's local `tts_player` queue can still hold 2–3 MP3s playing for another 5–8 s. The old code armed a fixed 800 ms timer on the server signal — mic opened mid‑playback.

**Shipped.** New `_spawn_mic_resume_waiter` in [`nao/ws_client.py`](../nao/ws_client.py):

1. Polls `tts_player.is_playing()` every 100 ms until queue drains → logs `local_tts_queue_empty`
2. Logs `playback_all_done`
3. Waits `MIC_GATE_GRACE_MS` (default 800 ms) for speaker cone to settle
4. Opens the mic → logs `mic_resume_after_playback`

Idempotent across back‑to‑back sentence chunks. 30 s outer cap so a wedged player can't lock the mic shut forever.

**Plus** clean recorder restart on `echo_reject` / legacy `reject_reason=self_echo` — `gate(True)` → 250 ms settle → `gate(False)` produces a fresh `stream.wav` so the tail of NAO's own voice doesn't keep getting re‑uploaded.

---

## D9. Action dispatch on recv thread vs worker

**Problem.** The action dispatcher (`nao_execute.dispatch`) was being called directly from the WS receive thread (`_handle_action`). NAOqi calls like `posture.goToPosture`, `motion.moveTo`, `motion.angleInterpolation`, and `runBehavior` block for seconds. While they ran, audio chunks couldn't be received and control frames (`barge_in`, `mic_resumed`) piled up in the WS queue.

**Shipped.** Single dedicated worker thread + `Queue`:

- `_handle_action` pushes `(name, args)` onto `_action_queue` and returns instantly
- `_action_worker_loop` (daemon thread named `nao-ws-actions`) drains it sequentially
- `_cancel_actions(reason)` drains pending + calls `behav_mgr.stopAllBehaviors()` on barge‑in, crisis lock, and shutdown

Sequential single worker (not a pool) because most NAOqi behaviors take exclusive joint resource locks — two body moves racing for HeadYaw is worse than serializing.

---

## D10. Blocking vs non‑blocking behavior calls

**Problem.** `ALBehaviorManager.runBehavior(name)` blocks the caller until the animation finishes. A 15‑second Choregraphe dance pack would freeze the action worker, which means a barge‑in mid‑dance still waits for the dance to end.

**Shipped.** Every behavior call uses `startBehavior` (non‑blocking). The legacy `blocking=True` kwarg on `_run_first_available` is preserved for ABI but is now a no‑op. Cancellation via `stopAllBehaviors`.

**Caveat.** `stopAllBehaviors()` does not stop raw `ALMotion.angleInterpolation` or `motion.moveTo`. Custom angle‑interp gestures are short (≤ 1.5 s) so they finish before the next worker item, but a longer custom move would need `motion.killTasks()` which freezes joints abruptly. Deferred until measurement says it's needed.

---

## D11. Router prompt allowing self‑answer

**Problem.** User asked "Now can you see who I am?" Vision returned `vision_status=success` with a perfect summary. But the **router** answered the question itself: "I don't have the ability to recognize or see your face." Router prompt had ZERO sensory grounding rules and no explicit handoff trigger for visual questions.

**Shipped.** [`server/agents/router.py`](../server/agents/router.py) extended:

- **Sensory grounding rule** — explicit "you have a microphone, camera, speakers, motors. NEVER deny senses."
- **Visual‑question handoff triggers** — "can you see me", "what am I wearing", "do you recognize me" → always handoff to chat
- Chat agent prompt (`server/agents/chat.py`) extended with face‑recognition behavior: read `[USER ...]` block, answer `returning=true name=X` → "Welcome back, X" vs `returning=false` → "I see you, haven't learned your face yet. What's your name?"

---

## D12. Crisis reply tone

**Problem.** Original hotline reply opened with "I hear you, and I'm really glad you're telling me." Tone‑deaf when a user has just expressed wanting to harm themselves — they're not in a place to be congratulated for opening up.

**Shipped.** Reword in [`server/safety.py`](../server/safety.py):

> "I hear you. What you're carrying sounds really heavy, and you don't have to hold it alone. Please reach out to someone who can stay with you right now — you can call or text 988 in the US for the Suicide and Crisis Lifeline, any time, day or night. Is there someone nearby you can be with too?"

Validates without praising. Names 988 + 24/7 availability (mandatory). Bridges to in‑person support.

---

## See also

- [PRD v2](PRD_v2.md) — full spec, 9 phases
- [Phase 0.5 spike](spike_results.md) — transport benchmark
- [Phase 1 task map](PHASE_1_TASK_MAP.md) — FastAPI + WS migration
- [Phase 4 task map](PHASE_4_TASK_MAP.md) — embodiment + sound localization
- [Phase 6 task map](PHASE_6_TASK_MAP.md) — vision + camera consent
- [Phase 9 task map](PHASE_9_TASK_MAP.md) — observability + tests
