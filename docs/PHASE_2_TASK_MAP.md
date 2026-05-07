# Phase 2 — Task Map & Contracts

> **VAD + Echo Hardening.** Builds on Phase 1's WS transport. The Phase 1 audio gate (`ALAudioDevice.unsubscribe`) is already in. Phase 2 tunes VAD for noisy rooms, strengthens echo, and adds an end-of-utterance arbiter that combines robot + server signals.

PRD sections to read first: PRD_v2.md Phase 2 + Phase 1 task map's frame envelope (`end_of_utterance` payload).

## Branch policy
Same as Phase 1. Worktree per agent off `dev/architecture-rework`. Commit message: `[Phase 2] <slug>: <summary>`. Do NOT modify `requirements.txt` — declare deps in commit message; consolidator merges.

## Worktree-level file ownership

| Slug | Files OWNED |
|------|-------------|
| `robot-vad` | `nao/audio_handler.py` (rewrite VAD section in place) |
| `server-eou-arbiter` | `server/app_ws.py` (surgical edits — preserve all existing routes & frame handling) |
| `server-silero` | `server/vad_silero.py` (extend in place) |
| `semantic-endpoint` | `server/semantic_endpoint.py` (extend in place) |
| `tests-vad` | `server/tests/test_vad_eou.py` (NEW), `server/tests/test_echo_regression.py` (extend) |

## Contracts

### Adaptive ambient floor (robot-vad)
- Maintain a rolling 30-second window of frame energies (compute from `ALAudioDevice.getFrontMicEnergy()` polled every 50 ms).
- `ambient_floor = percentile(window, 25)` — robust to occasional speech.
- `start_th = max(ambient_floor + 380, 700)`
- `keep_th = max(ambient_floor + 250, 420)`
- `silent_th = max(ambient_floor + 30, 260)`
- Recompute every 1 s, not per-frame, to avoid jitter.

### End-of-utterance signal (robot → server)
After TTS-aware mic-gate is open, when energy drops below `silent_th` for `trail_ms` (300 ms default), send via ws_client.push_control:
```jsonc
{ "subtype": "end_of_utterance",
  "data": { "robot_eou_hint": true, "energy_floor": <int>, "trail_ms": <int>, "duration_ms": <int> }}
```
**Remove the 10 s hard cap.** Allow up to 60 s of legitimate continuous speech (no cap on speech duration; only on silence trail).

### EoU arbiter (server-eou-arbiter, in app_ws.py)
Combine three signals to decide the turn is over:
1. **Silero VAD** confidence (server-side, on accumulated PCM): is speech currently present?
2. **Robot energy hint** from `end_of_utterance` control frame.
3. **Semantic endpoint** (optional, gated by `USE_SEMANTIC_ENDPOINT=1`): "is this transcript a complete thought?"

Decision logic (in `_should_finalize_turn(pcm_buffer, robot_hint, transcript_so_far)`):
- If Silero says no-speech for ≥ `MIN_SILENCE_MS` (default 600 ms): finalize.
- If robot hint received AND Silero confirms no-speech in last 200 ms: finalize.
- If Silero says no-speech for ≥ 250 ms AND `semantic_endpoint.is_complete_thought(transcript_so_far)` returns True: finalize early.
- Otherwise: wait.
- Hard ceiling: 60 s of utterance.

### Post-TTS cooldown (server-eou-arbiter)
After emitting the last `audio_chunk` frame and `tts_ended` control, **drop incoming `audio_chunk` frames** for `MIC_GATE_GRACE_MS + 400 ms` (catches reverb that survives the unsubscribe).

### Self-echo guard strengthening (server-eou-arbiter)
Currently `_is_self_echo` (in `_legacy_helpers.py`) does bigram-overlap > 0.6. Strengthen:
- ALSO compute substring match: if `transcript.lower()` is a substring of any sentence emitted in the last TTS reply (stored as `_LAST_REPLY_CHUNKS[username]: list[str]`), reject.
- Maintain `_LAST_REPLY_CHUNKS` per session — append every sentence-text passed to TTS synthesis.
- The guard runs BEFORE `_run_agent` is called (i.e., still inside the existing `_transcript_reject_reason` chain).

### Server Silero promotion (server-silero)
Currently lazy-loaded sanity check. Promote:
- Stream incremental PCM into Silero (process every 480 samples / 30 ms at 16 kHz).
- Public API:
  ```python
  class StreamingSilero:
      def feed(self, pcm_bytes: bytes) -> None: ...
      def is_speech_now(self) -> bool: ...
      def silence_duration_ms(self) -> int: ...
      def reset(self) -> None: ...
  ```
- Adaptive threshold: cluster the last 60 s of confidence values; pick the threshold at the valley between speech / non-speech bimodal distribution. Fall back to 0.4 if distribution isn't bimodal.

### Semantic endpoint upgrade (semantic-endpoint)
- Replace synchronous OpenAI call with async-friendly version using `asyncio.to_thread`.
- LRU cache keyed by transcript string (size 256). 80%+ of repeated phrases skip the LLM call.
- Tighten prompt to a single-token Yes/No grammar (use `temperature=0`, `max_tokens=1`).
- Public API:
  ```python
  async def is_complete_thought(transcript: str) -> bool
  ```

## Reused-as-is (do not modify)
- `server/safety.py:crisis_check`
- `server/motion_trigger.py:detect`
- `server/openai_tts.py:synthesize`
- `nao/audio_module.py:NaoAudioStreamer.gate`
- `nao/ws_client.py:NaoWsClient.push_control`
- All Phase 1 logging + metrics

## Latency phase labels (additions for Phase 2)
Add to the histogram in `server/metrics.py`:
- `vad_silero_decide` — time from PCM in to is-speech decision
- `eou_arbiter` — time spent in arbitration logic per check
- `semantic_endpoint_call` — time for is_complete_thought (cache miss case)

## Definition of done (per agent)
1. Files compile (`python -m py_compile <file>`).
2. Robot-side py2.7 syntax (no f-strings, no type hints; AST parse OK).
3. No new public symbols added without docstrings.
4. Final commit message format: `[Phase 2] <slug>: <summary>`.
5. Final report lists files written, LOC delta, deps declared, contract questions, summary.
