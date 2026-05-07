<!--
title: Phase 3 — Task Map & Contracts
tags: [phase-3, task-map, wake, face-detection, state-machine, leds]
related: [DECISIONS, PRD_v2, PHASE_4_TASK_MAP, PHASE_8_TASK_MAP]
phase: "3"
status: shipped
-->

# Phase 3 — Task Map & Contracts

> **Hybrid Wake: Face-First with Word Fallback.** Builds on Phase 1 transport + Phase 2 VAD. Replaces wake-word-only with passive face detection that wakes on engagement signals (gaze, proximity, sustained face, speech, or keyword fallback).

PRD section to read first: PRD_v2.md Phase 3 (the AWARE state machine table is non-negotiable).

## Branch policy
Worktree per agent off `dev/architecture-rework`. Commit message: `[Phase 3] <slug>: <summary>`. Don't touch `requirements.txt` — declare deps in commit message.

## Worktree-level file ownership

| Slug | Files OWNED |
|------|-------------|
| `wake-state-machine` | `nao/wake_state.py` (NEW), `nao/utils/exit_detection.py` (preserve, no edits — read only) |
| `face-detection-extend` | `nao/utils/face_naoqi.py` (extend in place; preserve `learn_new_face_naoqi`, `recognise`, `clear_db`) |
| `led-driver` | `nao/leds.py` (NEW) |
| `main-rewire` | `nao/main.py` (REWRITE entry to boot wake_state instead of WS client directly) |
| `server-wake-event` | `server/app_ws.py` (surgical edit — add `wake_event` control frame handler, greeting flow, 24 h session resume) |
| `wake-tests` | `server/tests/test_wake_state.py` (NEW), `server/tests/test_face_detection.py` (NEW) |

## Wake state machine — exact contract (from PRD v2 §Phase 3 + Section 5 of fixes)

```
IDLE       → eyes dim gray, downward gaze
             trigger: face confidence ≥ 0.35 AND distance 0.3–1.5 m AND angle ±60°
             →
AWARE      → face detected, NOT YET ENGAGED
             eyes soft blue (animacy cue, NO chime, NO speech)
             head tracks face gently (face-following, not greeting)
             evaluate engagement gates concurrently:
               • mutual gaze sustained ≥ 1.5 s, OR
               • distance < 1.0 m stable for ≥ 1.0 s, OR
               • face confidence ≥ 0.5 sustained ≥ 2.0 s with frontal angle (±30°), OR
               • speech onset detected (Phase 2 EoU signaling speech start), OR
               • "hey nao" keyword via ALSpeechRecognition fallback
             if no gate fires within 8 s OR face lost → IDLE silently
             →
ENGAGED    → engagement gate fired
             soft chime (80 dB, 200 ms), eyes solid blue
             open WS session, send `wake_event` control frame with face_id + which gate
             →
LISTENING  → either robot greeted (server-driven from face_id), or user spoke first
             eyes cyan, gaze aversion every 2.5 s with ±8° head yaw
             stream PCM (Phase 1 transport)
SPEAKING   → TTS playing
             eyes warm yellow, mouth animation
             mic gated (Phase 1 ALAudioDevice unsubscribe + Phase 2 cooldown)
```

## Public APIs

### `nao/wake_state.py` — `class WakeStateMachine`
```python
class WakeStateMachine(object):
    """Continuous state machine. Owned + driven by main.py.

    Subscribes to ALFaceDetection at 30 fps; runs the state transitions
    above; calls hooks on state changes. Clean shutdown via stop().
    """

    STATES = ("IDLE", "AWARE", "ENGAGED", "LISTENING", "SPEAKING")

    def __init__(self, nao_ip, nao_port,
                 leds, fallback_word_listener,
                 on_engaged, on_lost, on_listening, on_speaking_done,
                 face_min_conf=0.35, face_max_distance_m=1.5,
                 face_max_angle_deg=60.0,
                 aware_timeout_s=8.0, gaze_required_s=1.5,
                 proximity_required_s=1.0, sustained_conf=0.5,
                 sustained_required_s=2.0, sustained_angle_deg=30.0):
        # leds = nao.leds.LedDriver instance
        # fallback_word_listener = wake_listener.WakeListener (existing)
        # on_engaged(face_id, gate_name, confidence, distance_m) — called once per session
        # on_lost() — called on AWARE timeout / face loss
        # on_listening() — called when transitioning into LISTENING
        # on_speaking_done() — called when SPEAKING→LISTENING

    def start(self):
        # blocks; runs until stop()

    def stop(self):
        # idempotent

    def current_state(self) -> str: ...

    def set_state(self, state: str) -> None:
        # external trigger (e.g. server says crisis_lock — force LISTENING termination)
```

### `nao/leds.py` — `class LedDriver`
```python
class LedDriver(object):
    """NAOqi LED helpers. Wraps ALLeds (with ALProxy fallback)."""

    EYES_GROUP = "FaceLeds"            # full eye RGB ring
    CHEST_GROUP = "ChestLeds"
    EAR_LEFT_GROUP = "EarLeds"

    # Color presets (R, G, B floats 0-1)
    COLOR_GRAY     = (0.10, 0.10, 0.12)   # IDLE
    COLOR_SOFT_BLUE = (0.10, 0.30, 0.70)  # AWARE
    COLOR_SOLID_BLUE = (0.20, 0.50, 1.00) # ENGAGED
    COLOR_CYAN     = (0.10, 0.80, 0.95)   # LISTENING
    COLOR_YELLOW   = (1.00, 0.80, 0.10)   # SPEAKING
    COLOR_GREEN    = (0.10, 0.90, 0.30)   # camera-active (Phase 6)

    def __init__(self, nao_ip, nao_port=9559): ...

    def fade(self, group, rgb, duration_s=0.4): ...

    def pulse(self, group, rgb, period_s=1.0, count=None): ...

    # Convenience
    def set_idle(self): self.fade(EYES_GROUP, COLOR_GRAY, 0.6)
    def set_aware(self): self.fade(EYES_GROUP, COLOR_SOFT_BLUE, 0.4)
    def set_engaged(self): self.fade(EYES_GROUP, COLOR_SOLID_BLUE, 0.2)
    def set_listening(self): self.fade(EYES_GROUP, COLOR_CYAN, 0.3)
    def set_speaking(self): self.fade(EYES_GROUP, COLOR_YELLOW, 0.2)
    def chime(self): ... # plays an 80 dB, 200 ms chime via ALAudioPlayer
```

### `nao/utils/face_naoqi.py` — extensions
Existing functions stay intact. Add:
```python
def detect_faces_with_geometry(face_detection, memory, max_age_ms=200):
    """Return list of {face_id, name, confidence, distance_m, yaw_deg, pitch_deg}.

    Reads ALMemory["FaceDetected"] (the standard ALFaceDetection event payload),
    extracts the geometry block (head angles + face position in image plane),
    and computes approximate distance using the ALFaceDetection 'face size in
    image' heuristic and the known camera FOV. Returns at most one face per
    face_id (closest if duplicate)."""

def closest_face(faces):
    """Pick the closest face by distance_m; ties broken by highest confidence."""

def is_mutually_gazing(face, yaw_tolerance_deg=15, pitch_tolerance_deg=15):
    """Returns True if the face is roughly head-on (eyes pointed at NAO).
    Approximation: head yaw + pitch within tolerance."""
```

### `server/app_ws.py` — `wake_event` handler
Add to the existing control frame router:
```python
elif subtype == "wake_event":
    face_id = data.get("face_id")
    gate = data.get("gate")
    confidence = data.get("confidence", 0.0)
    distance_m = data.get("distance_m", 0.0)
    # 1. Log to safety_events (use existing session.log_safety helper)
    # 2. Resume SQLiteSession if same face_id seen in last 24 h (else create new)
    # 3. Generate greeting via _generate_greeting helper (existing in server.py:910)
    #    with face context; emit one audio_chunk + transcript control
    # 4. Set per-session state to LISTENING-ready
```

## Wake-event payload (robot → server)
```jsonc
{ "subtype": "wake_event",
  "data": { "face_id": "abc123",
            "gate": "mutual_gaze" | "proximity" | "sustained_face" | "speech" | "keyword",
            "confidence": 0.62,
            "distance_m": 0.9,
            "is_returning_user": true } }
```

## Latency phase labels (additions)
- `face_detect` — time per face detection cycle
- `wake_to_engaged` — IDLE → ENGAGED transition (engagement gate latency)
- `engaged_to_first_audio` — ENGAGED → first server audio_chunk

## Reused-as-is
- `nao/wake_listener.py` — kept verbatim; `WakeStateMachine` instantiates it for the keyword fallback.
- `nao/utils/face_naoqi.py:learn_new_face_naoqi`, `recognise`, `clear_db` — preserved.
- `nao/audio_handler.py:AdaptiveVad` (Phase 2) — `WakeStateMachine` queries it for "speech onset" engagement gate.
- All Phase 1/2 server-side modules.

## Definition of done
1. `python -m py_compile` succeeds on all touched files.
2. Robot files: py2.7 syntax (no f-strings, no type hints).
3. `WakeStateMachine` passes a synthetic-input self-test (mocked face detector).
4. `nao/leds.py` instantiates without naoqi (guarded), exposes the documented API.
5. `nao/main.py` boots into `WakeStateMachine.start()`; on `on_engaged`, opens `NaoWsClient` and starts a session.
6. Server `wake_event` handler logs + greets + resumes 24 h session.
7. Tests collect cleanly.
