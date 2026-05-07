# Phase 10.5 — Virtual NAO simulator

Drive the entire NAO Morgan Assist voice pipeline (mic → WS → server →
TTS → speaker) from a Mac, no robot required. Two ways to run it:

1. **Live mode** — `python sim/live_nao.py` opens your default mic and
   talks to the running server like the real robot would. Owned by the
   `live-nao-driver` worktree.
2. **Scenario mode** — `python -m sim.scenarios <name>` runs one of six
   scripted, headless scenarios against the FastAPI app in-process. No
   real audio. Owned by this worktree (`scenarios`).

This README covers scenario mode, the latency CSV, and how to add a new
scenario. The fake `naoqi` modules and the live driver are documented in
their own files (`sim/fake_naoqi.py`, `sim/live_nao.py`).

## Install

Mac dependencies (one time):

```bash
brew install ffmpeg portaudio
pip install sounddevice numpy pydub
# the scenarios themselves only need fastapi[testclient]:
pip install "fastapi[testclient]" httpx
```

The repo's `server/requirements.txt` already pins `fastapi`, `httpx`,
and the rest of the server-side stack — running scenarios against an
already-set-up dev env should just work without any extra install.

## Run a scenario

List the available scenarios:

```bash
python -m sim.scenarios
```

Run one:

```bash
python -m sim.scenarios 01_face_wake
```

Run all six in sequence (and aggregate exit code):

```bash
python -m sim.scenarios all
```

Each scenario prints its outcome (`ok` / `fail` / `timeout` / `skipped`),
its `details` dict (active agent, action names, timing), an ASCII
telemetry table, and the path to the latency CSV.

`skipped` outcomes happen when a sibling worktree (live-nao-driver,
fake-naoqi-mod) hasn't merged yet — the scenario detects the missing
dep and bails cleanly instead of failing the run.

### The six scenarios

| File | What it tests |
|------|---------------|
| `01_face_wake.py` | Face wake → `ready_to_listen` → first turn replies |
| `02_morgan_question.py` | "what is CS 491?" routes to the chatbot agent and calls `cs_navigator_search` |
| `03_therapy_turn.py` | Therapy intent routes to therapist; `observe_face` + `gesture {intent: "nod"}` enqueued |
| `04_barge_in.py` | Mid-TTS `barge_in` stops the player within 600 ms; follow-up speech is a new turn |
| `05_echo_bleed.py` | Echo of robot's reply does NOT trigger a second turn (post-TTS cooldown + substring guard) |
| `06_goodbye.py` | "goodbye" → robot-side `session_close` → server emits `session_end` |

Each scenario times out at 30 s. On timeout the outcome is `timeout` and
the partial telemetry rows are still written to CSV.

## Latency CSV

Default path: `~/nao_assist/sim_latency.csv`

Columns:

```
timestamp_iso, turn_idx, outcome, user_text, reply_preview,
<22 phase columns alphabetically — agent_complete, agent_first_token,
 action_dispatch, crisis_check, cs_navigator_call, e2e_user_to_complete,
 e2e_user_to_first_audio, engaged_to_first_audio, eou_arbiter,
 face_detect, gesture_dispatch, motion_trigger, semantic_endpoint_call,
 sound_localize_react, stt, tts_synth_first_chunk, tts_synth_total,
 vad, vad_silero_decide, vision_call, wake_to_engaged,
 wake_to_first_audio>
```

Phase column values are integer milliseconds, or empty string when the
scenario didn't time that phase for that turn.

The header is written exactly once (the first time the file is
created); subsequent runs append. To reset the file:

```bash
rm ~/nao_assist/sim_latency.csv
```

A quick way to look at the last 20 turns from the shell:

```bash
column -ts, -n ~/nao_assist/sim_latency.csv | tail -20
```

For deeper analysis:

```python
import csv, statistics, pathlib
rows = list(csv.DictReader(pathlib.Path("~/nao_assist/sim_latency.csv").expanduser().open()))
e2e = [float(r["e2e_user_to_first_audio"]) for r in rows if r["e2e_user_to_first_audio"]]
print("p50", statistics.median(e2e), "p95", statistics.quantiles(e2e, n=20)[-1])
```

## Add a new scenario

1. Drop a file under `sim/scenarios/` named `<NN>_<slug>.py` where `NN`
   is the next two-digit prefix and `slug` matches `[A-Za-z0-9_]+`.
2. Export `def run(driver, telemetry) -> dict`. Return shape:
   ```python
   {
       "scenario": "<name without .py>",
       "outcome":  "ok" | "fail" | "timeout" | "skipped",
       "details":  {...},          # whatever's useful
       "telemetry_rows": telemetry.rows,
   }
   ```
3. Use `driver.install_mocks(transcript=..., reply=..., active_agent=...,
   actions=...)` so STT/TTS/agent/safety calls are deterministic.
4. Drive the WS with the helpers on `Driver`: `inject_face`, `say`,
   `send_barge_in`, `send_session_close`, `expect(predicate, timeout_s)`,
   `assert_no_more_audio(timeout_s)`.
5. Wrap each turn with `telemetry.start_turn(turn_idx, user_text)` /
   `telemetry.mark(phase, ms)` / `telemetry.end_turn(outcome, reply)`.
   Use only phase keys from `server.metrics.ALLOWED_PHASES` — anything
   else logs a warning and is dropped from the CSV.
6. Make every wait bounded by the scenario's 30 s budget — pass
   `timeout_s=min(5.0, deadline - time.monotonic())` to `expect()`.
7. Catch `DriverUnavailable` → return `outcome="skipped"`. Catch
   `TimeoutError` → `outcome="timeout"`. Catch the rest → `outcome="fail"`.

Discovery is automatic — the runner scans `sim/scenarios/` for files
matching `^\d{2}_[A-Za-z0-9_]+\.py$`. No registration step.

## Known limitations

- **No real microphone or speaker.** Scenarios send synthetic silent
  PCM and rely on monkeypatched STT to drive the transcript path. Only
  `live_nao.py` (the live-nao-driver worktree) opens real audio devices.
- **No real motors.** Action frames are observed in the WS stream but
  never reach a robot. The `fake-naoqi-mod` worktree adds a console
  renderer for LEDs and joint angles.
- **No real face tracking.** `inject_face` ships a `wake_event` directly
  to the server — there's no `ALFaceDetection` event going through fake
  ALMemory in scenario mode.
- **No SSL.** `TestClient` mounts the app in-process; the WS frames are
  in-memory queues. The `NAO_SHARED_SECRET` header gate is honored
  (pass `secret=""` for an unauth'd dev server).
- **No real OpenAI / Pinecone calls.** Every scenario `install_mocks()`
  by default; if you want real-network behavior you'll have to undo
  those patches yourself.

## Files

| Path | Purpose |
|------|---------|
| `sim/scenarios/_driver.py` | `Driver` facade (connect_ws, inject_face, say, expect, assert_no_more_audio) |
| `sim/scenarios/__init__.py` | Registry + `python -m sim.scenarios <name>` CLI |
| `sim/scenarios/01–06_*.py` | The six scripted scenarios |
| `sim/scenarios/audio/` | Stub WAVs (silent + 440 Hz sine), generated on first import |
| `sim/telemetry.py` | `Telemetry` class — per-turn CSV + ASCII report |

## Verification

```bash
python -m py_compile sim/scenarios/*.py sim/telemetry.py sim/scenarios/_driver.py
python -c "from sim.scenarios import list_scenarios; print(list_scenarios())"
python -m sim.telemetry          # writes a temp CSV, prints the report, exits 0
```

If the FastAPI app is importable (i.e. you're on the v2 rework branch):

```bash
python -m sim.scenarios 01_face_wake
```

If not, scenarios skip cleanly with `outcome="skipped"`.
