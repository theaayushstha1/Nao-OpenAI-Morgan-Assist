<!--
title: Phase 4 — Task Map & Contracts
tags: [phase-4, task-map, embodiment, gestures, sound-localization, motors]
related: [DECISIONS, PRD_v2, PHASE_3_TASK_MAP, PHASE_5_TASK_MAP]
phase: "4"
status: shipped
-->

# Phase 4 — Task Map & Contracts

> **Active Embodiment.** Sound-source localization (head turns toward speaker), per-turn body-language gestures, idle breathing/gaze. Vessel → brain.

PRD: PRD_v2.md Phase 4.

## Branch policy
Worktree per agent off `dev/architecture-rework`. Commit message: `[Phase 4] <slug>: <summary>`. Don't touch `requirements.txt` — declare deps in commit message.

## Worktree-level file ownership

| Slug | Files OWNED |
|------|-------------|
| `server-gesture-tool` | `server/tools/nao_actions.py` (extend — add `gesture` tool), `server/agents/therapist.py` (prompt), `server/agents/chat.py` (prompt) |
| `robot-gesture-dispatch` | `nao/utils/nao_execute.py` (extend — handle `gesture` action with intent→motion table) |
| `robot-sound-localize` | `nao/sound_localize.py` (NEW) |
| `robot-idle-motion` | `nao/idle_motion.py` (NEW) |
| `embodiment-tests` | `server/tests/test_gesture.py` (NEW), `server/tests/test_sound_localize.py` (NEW) |

## Gesture intents (canonical set — used by all agents)
```
nod          : 2× head pitch nod, ~600 ms
shake        : head yaw shake, ~700 ms
lean_in      : torso forward 5°, ~1.2 s, holds during turn, returns on TTS end
lean_back    : torso back 3°, ~800 ms
open_arms    : both arms outward 30°, ~1 s
point_self   : right hand to chest, ~700 ms
point_listener : right arm extended toward last sound source, ~900 ms
shrug        : shoulders up + head tilt, ~600 ms
tilt_curious : head roll 12° + slight pause, ~500 ms
breath_deep  : chest breathing animation 1 cycle, ~3 s
```

## Public APIs

### `server/tools/nao_actions.py` — add `gesture` tool
```python
@function_tool
def gesture(ctx: RunContextWrapper, intent: str) -> str:
    """Perform a single body-language gesture during the current reply.
    Allowed intents: nod, shake, lean_in, lean_back, open_arms, point_self,
    point_listener, shrug, tilt_curious, breath_deep.
    Call this PARALLEL to speech (it does not block the audio).
    """
    # validate intent against canonical set
    # _enqueue(ctx, "gesture", {"intent": intent})
    return f"queued gesture: {intent}"
```

Add to `THERAPIST_ACTIONS` and to whatever `chat`'s tool list is. Existing 18 NAO action tools untouched.

### `nao/utils/nao_execute.py` — gesture dispatch table
Add a top-level mapping `_GESTURE_TABLE: dict[str, callable]` with one entry per intent. Each callable takes `(motion, posture, leds, sound_localize_module)` and runs the documented gesture using ALMotion.angleInterpolation or behavior calls.

Wire `dispatch(action_name, args, ...)` to recognise `"gesture"` and route to the table.

For `point_listener`, query `sound_localize.get_last_direction()` — if available, use that yaw; else fall back to looking forward.

### `nao/sound_localize.py` — `class SoundLocalizer`
```python
class SoundLocalizer(object):
    """Subscribes to ALSoundLocalization events; tracks the most recent
    speaker direction; can drive head turning via ALMotion.

    NAOqi's SoundLocalization fires events ~10 Hz with [time, [confidence, energy], [azimuth, elevation, _, _]]
    in robot frame.
    """

    def __init__(self, nao_ip, nao_port=9559, motion=None,
                 max_yaw_deg=60.0, max_pitch_deg=20.0,
                 turn_speed_dps=30.0, confidence_min=0.4):

    def start(self):
        # subscribes, starts background tracker thread

    def stop(self):
        # unsubscribes; idempotent

    def get_last_direction(self):
        # returns dict {azimuth_deg, elevation_deg, ts_ms, confidence} or None

    def turn_head_toward(self, azimuth_deg, elevation_deg=0.0):
        # ALMotion.angleInterpolation HeadYaw + HeadPitch, capped at max
```

Background thread: every event, if confidence >= min, store + optionally call `turn_head_toward` (toggle via `auto_track=True/False`).

### `nao/idle_motion.py` — `class IdleMotion`
```python
class IdleMotion(object):
    """Background subtle motion for IDLE / LISTENING states.

    - IDLE: breathing animation cycle (chest + shoulders), gaze drifts down/forward
    - LISTENING: gaze aversion every 2.5 s with ±8° head yaw

    Driven by external state callbacks from WakeStateMachine.
    """

    def __init__(self, nao_ip, nao_port=9559, motion=None, autonomous=None):

    def set_state(self, state):
        # state in {"idle", "listening", "off"}

    def stop(self):
        # idempotent
```

`autonomous` argument is `ALProxy("ALAutonomousLife")` if available — `IdleMotion` should respect autonomous-life state (don't fight it) by setting `setAutonomousAbilityEnabled("BackgroundMovement", False)` when active.

## Reused-as-is
- `server/openai_tts.py`, `server/safety.py`, `server/motion_trigger.py` — untouched.
- All Phase 1/2/3 modules.

## Latency phase labels (additions)
- `gesture_dispatch` — server tool call → action_queue append
- `sound_localize_react` — sound event → head-turn ALMotion call

## Definition of done
1. All `python -m py_compile` succeed.
2. Robot files: py2.7 syntax compliant.
3. Therapist prompt has explicit gesture-call examples.
4. Chat agent prompt has explicit gesture-call examples.
5. `nao_execute.dispatch("gesture", {"intent": "nod"}, ...)` runs in disabled-naoqi mode without raising.
6. `SoundLocalizer.get_last_direction()` returns None when not started.
7. Tests collect cleanly.
