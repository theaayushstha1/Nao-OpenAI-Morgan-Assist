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
    """Run a dance. `style` options: 'robot' (default funny dance),
    'taichi' (full Tai Chi routine, ~30s), 'slide' (a smooth slide).
    Aliases like 'hiphop'/'salsa'/'funny' map to the default funny dance."""
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
    """Start the follow-me behavior — NAO tracks and mirrors the user's
    movements. Runs until stop_follow is called or the user moves out of
    sight. Call this when the user says 'follow me', 'come with me', etc."""
    return _enqueue(ctx, "follow_movement", {})


@function_tool
def stop_follow(ctx: RunContextWrapper) -> str:
    """Stop the follow-me behavior. Call when the user says 'stop following',
    'stay there', 'enough following', etc."""
    return _enqueue(ctx, "stop_follow", {})


@function_tool
def learn_face(ctx: RunContextWrapper, name: str) -> str:
    """Teach NAO to recognize the user's face under a given name.

    Call this when the user says things like:
      • "Remember me as Aayush"
      • "My name is Aayush, learn my face"
      • "Save my face as Aayush"
      • "Learn my face"  (in which case ask for their name first)

    The name is the label NAO will use for this face going forward. Once
    learned, future sessions will recognize this person and the agent
    will see their name in the user context. Stored persistently in the
    NAOqi face database — survives reboots.

    Args:
      name: Short clean label for the face (1-2 words, no special chars).
    """
    clean = (name or "").strip()
    if not clean:
        return "I need a name to remember you by — can you tell me your name?"
    return _enqueue(ctx, "learn_face", {"name": clean})


@function_tool
def play_animation(ctx: RunContextWrapper, animation: str) -> str:
    """Play a named animation. Use this for any motion request that isn't
    covered by the specific tools (wave/nod/dance/follow). The robot maps
    the name to an installed behavior and falls back gracefully if missing.

    Common values the LLM should pick from based on user phrasing:
      Animals/dances: elephant, monkey, dragon, kungfu, taichi, slide, robot
      Emotions:       happy, sad, angry, surprised, proud, shy, winner,
                      laugh, bored, anxious, disappointed, embarrassed,
                      hurt, frustrated, mocker
      Body talk:      explain, show_sky, show_floor, look_around, stretch,
                      rest, drink, yawn, sneeze, hungry
      Strength:       show_muscle, bow

    If the user says 'do an elephant' you'd call this with animation='elephant'.
    Pass the literal noun the user used; the robot does fuzzy mapping.
    """
    return _enqueue(ctx, "play_animation", {"animation": (animation or "").strip().lower()})


# ───────── Phase 4: Body-language gestures ─────────
#
# Canonical, fixed-vocabulary gestures from PHASE_4_TASK_MAP.md. These are
# meant to be called *parallel* to TTS — short, shaped motions that punctuate
# speech (a nod on reflection, a lean-in on a question) rather than full
# routines. They append to the same actions_queue as the existing tools;
# nao_execute.py routes the `gesture` action to a per-intent motion table.

GESTURE_INTENTS: set[str] = {
    "nod",
    "shake",
    "lean_in",
    "lean_back",
    "open_arms",
    "point_self",
    "point_listener",
    "shrug",
    "tilt_curious",
    "breath_deep",
}


@function_tool
def gesture(ctx: RunContextWrapper, intent: str) -> str:
    """Perform a single body-language gesture during the current reply.

    Allowed intents (canonical set):
      nod            -- 2x head pitch nod, ~600 ms; reflective agreement
      shake          -- head yaw shake, ~700 ms; gentle disagreement / "no"
      lean_in        -- torso forward 5 deg, ~1.2 s; curiosity / engaged listening
      lean_back      -- torso back 3 deg, ~800 ms; giving the user space
      open_arms      -- both arms outward 30 deg, ~1 s; greeting / affirmation
      point_self     -- right hand to chest, ~700 ms; "I", introducing self
      point_listener -- right arm toward last sound source, ~900 ms; "you"
      shrug          -- shoulders up + head tilt, ~600 ms; uncertainty
      tilt_curious   -- head roll 12 deg + slight pause, ~500 ms; curious question
      breath_deep    -- chest breathing 1 cycle, ~3 s; calm / pacing cue

    Call this PARALLEL to speech -- it does not block the audio. You may call
    multiple gestures in one turn (e.g. nod while reflecting, then lean_in
    on the follow-up question).
    """
    cleaned = (intent or "").strip().lower()
    if cleaned not in GESTURE_INTENTS:
        return f"unknown gesture intent: {intent}"
    _enqueue(ctx, "gesture", {"intent": cleaned})
    return f"queued gesture: {cleaned}"


# ───────── Bundles ─────────

CHAT_ACTIONS = [
    stand_up, sit_down, kneel,
    wave_hand, wave_both_hands, nod_head, shake_head, clap_hands,
    move_forward, move_backward, turn_left, turn_right, spin,
    dance, change_eye_color, follow_movement, stop_follow,
    play_animation, learn_face,
    gesture,
]

# Therapy mode keeps grounding/empathy gestures plus a few playful actions
# so users who ask "do a dance" or "wave" mid-conversation aren't told NAO
# can't do anything physical. Heavy locomotion (move_forward/spin) stays
# out — clinical-feeling agents shouldn't pace around.
THERAPIST_ACTIONS = [
    set_led_color, nod_head, shake_head,
    wave_hand, wave_both_hands, clap_hands,
    dance, follow_movement, stop_follow,
    stand_up, sit_down,
    play_animation,
    gesture,
    # nao-therapy: learn_face is the agent-side path for "remember
    # me as Aayush" / "my name is Aayush" / "call me Aayush". The
    # motion_trigger fast-path catches the obvious patterns before
    # the LLM sees them; this is the fallback when the user phrasing
    # is more conversational ("oh by the way I'm Aayush, would you
    # remember me?").
    learn_face,
]

ALL_TOOL_NAMES = {t.name for t in CHAT_ACTIONS} | {"set_led_color"}
