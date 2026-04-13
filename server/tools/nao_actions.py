"""NAO action tools.

These are declared as regular Agents-SDK function tools, but their *implementation*
doesn't touch NAO. Instead they append a structured {name, args} record to the
shared `actions_queue` in the run context. After `Runner.run()` completes, the
caller reads the queue and returns it in the `/turn` response; NAO executes them
in order.

Keeping execution off the server lets the agent reason naturally ("I'll wave and
turn blue while saying hi") without us needing an RPC back to the robot mid-turn.
"""
from __future__ import annotations

from typing import Any

from agents import RunContextWrapper, function_tool


def _enqueue(ctx, name: str, args: dict[str, Any]) -> str:
    # ctx may be a dict (in unit tests) or a RunContextWrapper (at runtime).
    if isinstance(ctx, RunContextWrapper):
        store = ctx.context
    else:
        store = ctx
    store.setdefault("actions_queue", []).append({"name": name, "args": args})
    return "queued"


# ───────── Posture ─────────

@function_tool
def stand_up(ctx: RunContextWrapper) -> str:
    """Have NAO stand up from sitting or crouching."""
    return _enqueue(ctx, "stand_up", {})


@function_tool
def sit_down(ctx: RunContextWrapper) -> str:
    """Have NAO sit down from standing."""
    return _enqueue(ctx, "sit_down", {})


@function_tool
def kneel(ctx: RunContextWrapper) -> str:
    """Have NAO kneel on one knee."""
    return _enqueue(ctx, "kneel", {})


# ───────── Gesture ─────────

@function_tool
def wave_hand(ctx: RunContextWrapper, hand: str = "right", speed: float = 0.6) -> str:
    """Wave one hand. `hand` is 'left' or 'right'; `speed` is 0.1-1.0."""
    return _enqueue(ctx, "wave_hand", {"hand": hand, "speed": speed})


@function_tool
def wave_both_hands(ctx: RunContextWrapper) -> str:
    """Wave both hands."""
    return _enqueue(ctx, "wave_both_hands", {})


@function_tool
def nod_head(ctx: RunContextWrapper, times: int = 2) -> str:
    """Nod yes 1-5 times."""
    return _enqueue(ctx, "nod_head", {"times": max(1, min(5, times))})


@function_tool
def shake_head(ctx: RunContextWrapper, times: int = 2) -> str:
    """Shake no 1-5 times."""
    return _enqueue(ctx, "shake_head", {"times": max(1, min(5, times))})


@function_tool
def clap_hands(ctx: RunContextWrapper, times: int = 2) -> str:
    """Clap 1-5 times."""
    return _enqueue(ctx, "clap_hands", {"times": max(1, min(5, times))})


# ───────── Movement ─────────

@function_tool
def move_forward(ctx: RunContextWrapper, meters: float = 0.3) -> str:
    """Walk forward `meters` meters."""
    return _enqueue(ctx, "move_forward", {"meters": max(0.0, meters)})


@function_tool
def move_backward(ctx: RunContextWrapper, meters: float = 0.3) -> str:
    """Walk backward `meters` meters."""
    return _enqueue(ctx, "move_backward", {"meters": max(0.0, meters)})


@function_tool
def turn_left(ctx: RunContextWrapper, degrees: float = 45.0) -> str:
    """Turn left in place."""
    return _enqueue(ctx, "turn_left", {"degrees": max(0.0, degrees)})


@function_tool
def turn_right(ctx: RunContextWrapper, degrees: float = 45.0) -> str:
    """Turn right in place."""
    return _enqueue(ctx, "turn_right", {"degrees": max(0.0, degrees)})


@function_tool
def spin(ctx: RunContextWrapper, degrees: float = 360.0) -> str:
    """Spin in place."""
    return _enqueue(ctx, "spin", {"degrees": max(0.0, degrees)})


# ───────── Expression ─────────

@function_tool
def dance(ctx: RunContextWrapper, style: str = "robot") -> str:
    """Run a dance behavior. `style`: 'robot', 'hiphop', or 'salsa'."""
    return _enqueue(ctx, "dance", {"style": style})


@function_tool
def change_eye_color(ctx: RunContextWrapper, color: str = "white") -> str:
    """Set eye LED color. Options: red, green, blue, yellow, purple, white."""
    return _enqueue(ctx, "change_eye_color", {"color": color})


@function_tool
def set_led_color(ctx: RunContextWrapper, color: str = "white") -> str:
    """Alias for change_eye_color used by the therapist agent for mood cues."""
    return _enqueue(ctx, "change_eye_color", {"color": color})


@function_tool
def follow_movement(ctx: RunContextWrapper) -> str:
    """NAO mirrors the user's upper-body motions."""
    return _enqueue(ctx, "follow_movement", {})


# ───────── Bundles ─────────

CHAT_ACTIONS = [
    stand_up, sit_down, kneel,
    wave_hand, wave_both_hands, nod_head, shake_head, clap_hands,
    move_forward, move_backward, turn_left, turn_right, spin,
    dance, change_eye_color, follow_movement,
]

THERAPIST_ACTIONS = [
    set_led_color, nod_head,
]

ALL_TOOL_NAMES = {t.name for t in CHAT_ACTIONS} | {"set_led_color"}
