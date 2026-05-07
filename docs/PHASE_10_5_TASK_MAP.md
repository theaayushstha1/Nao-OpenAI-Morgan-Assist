# Phase 10.5 — Task Map & Contracts

> **Virtual NAO simulator.** Drives the entire voice pipeline (mic → WS → server → TTS → speaker) from a Mac, without the physical robot. Replaces nothing on the robot side. Lives entirely under `sim/`. Used to stabilize the rework while NAO is unavailable, and as the substrate that makes Phase 10 fixes verifiable.

PRD: this task map is a Phase 10.5 addition (not in PRD v2). Approved by operator 2026-05-07.

## Goals

1. `python sim/live_nao.py` — talk into Mac mic, hear replies on Mac speaker, exactly as if you were standing in front of NAO.
2. Headless scripted scenarios — face wake, Morgan question, therapy, echo bleed, barge-in, goodbye.
3. Per-scenario latency telemetry (CSV/JSON): STT time, agent first-token, agent complete, TTS first-audio, TTS complete, e2e.
4. CI-runnable: `pytest server/tests/test_virtual_robot_e2e.py` drives the full pipeline against a uvicorn instance.

## Non-goals

- Mic SNR / speaker acoustic fidelity — those are NAO-hardware-only.
- Motor accuracy — fake motion just logs intended joint angles.
- Real face tracking — fake injects synthetic detection events.
- Real sound source localization — fake provides scripted azimuths.

## Branch policy

Worktree per agent off `architecture-rework-v2`. Commit message: `[Phase 10.5] <slug>: <summary>`. Don't touch `requirements.txt` — declare deps in commit message; consolidator merges.

## File ownership

| Slug | Files OWNED |
|------|-------------|
| `fake-naoqi-mod` | `sim/__init__.py` (NEW), `sim/fake_naoqi.py` (NEW), `sim/leds_console.py` (NEW), `sim/echo_sim.py` (NEW) |
| `live-nao-driver` | `sim/live_nao.py` (NEW), `sim/audio_io.py` (NEW) |
| `scenarios` | `sim/scenarios/__init__.py` (NEW), `sim/scenarios/*.py` (NEW, 6 scenarios), `sim/telemetry.py` (NEW), `sim/README.md` (NEW) |
| `e2e-test` | `server/tests/test_virtual_robot_e2e.py` (NEW), `sim/conftest.py` (NEW) |

## Public APIs

### `sim/fake_naoqi.py`
Fakes the NAOqi surfaces actually imported by `nao/*` files. Read each robot-side file to find them. The minimum set:

```python
class FakeBroker: ...           # provides ALProxy lookup
class ALProxy:                   # constructor: ALProxy(name, ip, port)
                                 # routes to FakeAL{TextToSpeech, AudioDevice,
                                 # AudioPlayer, AudioRecorder, FaceDetection,
                                 # Motion, RobotPosture, Leds, Memory,
                                 # SpeechRecognition, SoundLocalization,
                                 # AutonomousLife, BehaviorManager}
class ALModule: ...              # base class for our ALAudioDevice subscriber

# Public installer — call before importing any nao/ module
def install_into_sys_modules(echo_sim=None, leds_renderer=None, on_event=None) -> None:
    """Inject `naoqi` and `qi` into sys.modules pointing at our fakes."""
```

Each fake AL class records calls to a `.calls` list AND emits to a registered `on_event(kind, data)` callback so scenarios can assert/observe.

`FakeALAudioDevice.subscribe(name)` should accept the ALModule pattern from `nao/audio_module.py` and forward synthesized PCM frames (from `audio_io` or scenarios) to the registered module's `processRemote` method.

`FakeALMemory` exposes:
- `getData(key)` — returns last-set value or None.
- `subscribeToEvent(name, module, callback)` — for ALSoundLocalization-style polling.
- `inject(key, value)` — test hook to set ALMemory values (e.g., face detection events).

`FakeALFaceDetection` accepts injected face events; `FakeALSoundLocalization` accepts injected azimuth events.

### `sim/leds_console.py`
- ANSI-colored terminal renderer.
- On every `FakeALLeds.fadeRGB(group, r, g, b, duration)` call, prints a single line:
  ```
  [leds] FaceLeds      → █████  blue (0.20, 0.50, 1.00)  (over 0.20s)
  [leds] RightEarLeds  → █      green (0.10, 0.90, 0.30) (over 0.05s)
  ```
- Color the output bars with the actual RGB to make state transitions readable.
- Keep an in-memory `current_state` dict for scenario assertions.

### `sim/echo_sim.py`
- `EchoSimulator(delay_ms=80, gain=0.10)` — mixes a fraction of `playFile()` audio back into the next `processRemote` PCM frames after the configured delay.
- Used by scenarios that exercise the echo guard.
- When disabled (default), no-op.

### `sim/audio_io.py`
- `MicCapture(sample_rate=16000)` — uses `sounddevice` (preferred) or `pyaudio` fallback to capture from default Mac mic; yields 20 ms PCM16 frames.
- `SpeakerOut()` — plays MP3/WAV bytes via `sounddevice` or `playsound` fallback.
- Both are no-op-able for headless scenarios.

### `sim/live_nao.py`
The interactive entry point. Steps:
1. `fake_naoqi.install_into_sys_modules(...)` with echo_sim disabled by default.
2. Boot uvicorn `server.app_ws:app` in a background thread (or assume operator has it running).
3. Start `nao/main.py` in the same process — it'll import naoqi → gets the fakes.
4. `MicCapture` → `FakeALAudioDevice.subscribe()` consumer → forwards PCM to the WS sender.
5. `FakeALAudioPlayer.playFile()` → `SpeakerOut`.
6. Headless face injection: a hotkey (e.g., `f`) injects a face detection event so wake fires without needing a real camera.
7. Headless head-touch: a hotkey (e.g., `t`) injects an `ALMemory["FrontTactilTouched"] = 1` event for barge-in tests.
8. Live LED state in the terminal (via `leds_console`).
9. Latency telemetry per turn appended to `~/nao_assist/sim_latency.csv`.
10. Ctrl-C clean shutdown.

Print on startup:
```
=== Virtual NAO ===
mic: <device name> @ 16 kHz
speaker: <device name>
fake face_id: aayush
hotkeys: [f] face wake  [t] head touch  [b] barge-in  [q] quit
==================
```

### `sim/scenarios/`
Each scenario is a `.py` file exposing `run(client, telemetry) -> dict`. Six required:

| File | Tests |
|------|-------|
| `01_face_wake.py` | Inject known face → wake_event → ready_to_listen → speak "hello" → reply audio. |
| `02_morgan_question.py` | Wake → "what is CS 491?" → assert chatbot agent picked + cs_navigator_search call. |
| `03_therapy_turn.py` | Wake → "I'm feeling anxious" → assert therapist agent + observe_face called + nod gesture. |
| `04_barge_in.py` | Wake → ask question → speak again before TTS done → assert barge-in stopped player. |
| `05_echo_bleed.py` | EchoSimulator on. Wake → reply plays → mic picks up echo → assert no second turn fires. |
| `06_goodbye.py` | Wake → "goodbye" → assert exit_detection or session_close. |

Each scenario emits its own row to telemetry. README documents how to run them.

### `sim/telemetry.py`
```python
class Telemetry:
    def __init__(self, out_csv="~/nao_assist/sim_latency.csv"): ...
    def start_turn(self, turn_idx): ...
    def mark(self, phase: str, ms: float): ...
    def end_turn(self, outcome: str): ...
    def report(self) -> str: ...   # printable table
```

Phase keys aligned with `server/metrics.py:ALLOWED_PHASES` (22 labels). At least these rows always populated: `stt`, `agent_first_token`, `agent_complete`, `tts_synth_first_chunk`, `e2e_user_to_first_audio`, `e2e_user_to_complete`.

### `server/tests/test_virtual_robot_e2e.py`
- Boots uvicorn `server.app_ws:app` on a free port via pytest fixture.
- Calls `fake_naoqi.install_into_sys_modules()`.
- Runs each scenario from `sim/scenarios/` as a separate test.
- Asserts: WS frames appear in expected order; outcome from telemetry; latency p95 within sane bounds (e.g., < 5 s with mocked OpenAI).
- For OpenAI calls: provide an `OPENAI_API_KEY=fake` mode that monkeypatches the OpenAI client to return canned responses. Real API calls only when explicitly enabled via env.

## Reused-as-is
- `server/app_ws.py` and the entire server pipeline.
- `nao/*` modules — should "just work" against the fakes if our fakes match the documented NAOqi semantics. Any mismatch surfaces as a contract question.

## Definition of done
1. `python -m py_compile sim/*.py sim/scenarios/*.py server/tests/test_virtual_robot_e2e.py` succeeds.
2. `python sim/live_nao.py` boots without naoqi installed.
3. Six scenario files exist and run against a mocked-OpenAI mode.
4. `pytest server/tests/test_virtual_robot_e2e.py -q` passes (mocked) or skips cleanly (when uvicorn or sounddevice unavailable).
5. Latency CSV is generated on every run.
6. README documents: install, how to run live, how to run scenarios, how to interpret CSV.
7. New deps declared (likely: `sounddevice`, `numpy`, possibly `pynput` for hotkeys).
