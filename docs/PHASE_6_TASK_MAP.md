<!--
title: Phase 6 — Task Map & Contracts
tags: [phase-6, task-map, vision, camera-consent, privacy, gpt4o]
related: [DECISIONS, PRD_v2, PHASE_5_TASK_MAP, PHASE_7_TASK_MAP]
phase: "6"
status: shipped
-->

# Phase 6 — Task Map & Contracts

> **Therapist Vision-On.** Default camera consent ON, debug the broken observe_face vision call, add visible green-LED capture cue, "stop watching me" pattern-trigger, first-turn audible heads-up.

PRD: PRD_v2.md Phase 6.

## Branch policy
Worktree per agent off `dev/architecture-rework`. Commit message: `[Phase 6] <slug>: <summary>`.

## File ownership

| Slug | Files OWNED |
|------|-------------|
| `vision-debug` | `server/tools/emotion.py` (debug observe_face vision call), `server/config.py` (extend with VISION_MODEL), `server/agents/therapist.py` (prompt — observe_face FIRST every turn) |
| `camera-consent` | `server/session.py` (default → 1; helper for is_first_turn), `server/migrations/0001_camera_default_on.py` (NEW), `server/app_ws.py` (first-turn announce on new sessions where camera_consent=1) |
| `stop-watching` | `server/motion_trigger.py` (extend with stop_watching_me + camera_on patterns), `server/tools/skills_tools.py` (enable_camera + disable_camera function tools) |
| `green-led-cue` | `nao/utils/camera_capture.py` (extend snap_quick to flash green ear LED for 150 ms during capture) |
| `vision-tests` | `server/tests/test_camera_consent.py` (NEW), `server/tests/test_observe_face.py` (NEW), `server/tests/test_stop_watching_pattern.py` (NEW) |

## Public APIs

### `server/tools/emotion.py:observe_face`
- Debug existing call: log full request payload size in dev mode, log response in dev mode.
- Use `config.VISION_MODEL` (default `"gpt-4o"`; can be set to `"gpt-5"` or whatever's GA when this lands).
- Send the JPEG bytes correctly base64-encoded with the right MIME (`image/jpeg`).
- Prompt: "Briefly describe the user's affect, eye contact, and posture in ≤30 words. Be observational; don't diagnose."
- On any error: log + return `"unable to observe right now"` (never raise).

### `server/config.py` additions
```python
VISION_MODEL = os.environ.get("VISION_MODEL", "gpt-4o")
CAMERA_DEFAULT_ON = os.environ.get("CAMERA_DEFAULT_ON", "1") == "1"
CAMERA_ANNOUNCE_TEXT = os.environ.get(
    "CAMERA_ANNOUNCE_TEXT",
    "Heads up — my camera is on for this conversation. Say 'stop watching me' anytime."
)
```

### `server/session.py`
- `get_camera_consent(username)` — default → 1 (was 0). Migration script handles existing rows.
- `is_first_turn(session_id)` — boolean helper.

### `server/migrations/0001_camera_default_on.py`
- Idempotent script that: (a) sets `user_prefs.camera_consent` column default to 1 for new rows, (b) leaves existing rows untouched (operator policy).
- Run on app boot if `CAMERA_DEFAULT_ON=1` AND first-time migration.
- Records its run in a tiny `migrations` table to avoid re-running.

### `server/app_ws.py` — first-turn announce
On `wake_event` or first turn of a session, if `camera_consent=1` AND `is_first_turn(session_id)`:
- TTS the `CAMERA_ANNOUNCE_TEXT` and send as one `audio_chunk`.
- Mark first-turn-announce as done in session state.

### `server/motion_trigger.py` additions
Add 2 trigger entries:
```python
("disable_camera", {}, "Got it, camera off.", [
    "stop watching me", "stop looking at me", "turn off the camera",
    "camera off", "no camera", "stop watching",
]),
("enable_camera", {}, "Camera back on.", [
    "turn camera back on", "camera on", "you can watch again",
    "turn the camera on",
]),
```

### `server/tools/skills_tools.py` additions
```python
@function_tool
def disable_camera(ctx: RunContextWrapper) -> str:
    """Disable camera for this session (and persist to user_prefs)."""

@function_tool
def enable_camera(ctx: RunContextWrapper) -> str:
    """Enable camera for this session (and persist to user_prefs)."""
```

Both call existing `session.set_camera_consent(username, value)` and emit a control frame `{ subtype: "camera_state", data: { enabled: bool } }` so the robot can light/extinguish the green LED.

### `nao/utils/camera_capture.py:snap_quick`
- Wrap the existing capture call: `LedDriver.fade(LedDriver.EAR_RIGHT_GROUP, COLOR_GREEN, 0.05)`, capture, `fade(EAR_RIGHT, COLOR_DEFAULT, 0.1)` — total ~150 ms green-on.
- LedDriver instance is passed in (don't reach for a global). New optional kwarg `leds=None`; when None, just capture without the LED flash (back-compat).

## Reused-as-is
- `server/safety.py`, `server/openai_tts.py` — untouched.

## Latency phase labels (additions)
- `vision_call` — observe_face round-trip

## Definition of done
1. Compile checks pass.
2. Migration is idempotent.
3. observe_face works against a mocked OpenAI vision response (fixture).
4. green-LED flash duration ≤ 200 ms.
5. Pattern triggers don't false-fire on benign speech.
6. Tests collect.
