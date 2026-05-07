# -*- coding: utf-8 -*-
"""Dispatch {name, args} records from the server to naoqi calls on NAO (Py 2.7).

Phase 4 adds a body-language ``gesture`` action with 10 canonical intents
(nod, shake, lean_in, lean_back, open_arms, point_self, point_listener,
shrug, tilt_curious, breath_deep). Each intent maps to a callable in
``_GESTURE_TABLE`` that runs the gesture using ``ALMotion.angleInterpolation``
and the documented duration from ``docs/PHASE_4_TASK_MAP.md``. Existing 18
action handlers (``stand_up`` ... ``play_animation``) are untouched so that
``run(action, session, motion, posture, leds, behav_mgr, tts)`` keeps the
exact contract ``conversation.py`` already relies on.

A new ``dispatch(action_name, args, **kwargs)`` entry point is also exposed
because Phase 1's ``ws_client`` / ``main.py`` look for ``dispatch`` first and
fall back to ``run``. The new entry covers gestures plus the new
``sound_localize`` kwarg used by ``point_listener``.
"""
from __future__ import print_function


_EYE_COLORS = {
    "red": 0xFF0000, "green": 0x00FF00, "blue": 0x0000FF,
    "yellow": 0xFFFF00, "purple": 0x800080, "white": 0xFFFFFF,
}


# Maps the style argument from the LLM to an actual installed behavior.
# Falls back to FunnyDancer_1 (built-in stock animation, always present) so
# requests like "dance hiphop" don't silently fail just because the optional
# Choregraphe pack isn't on the robot.
_DANCE_BEHAVIORS = {
    "taichi":   "taichi-dance-free",
    "tai-chi":  "taichi-dance-free",
    "tai chi":  "taichi-dance-free",
    "slide":    "animations/Stand/Waiting/FunnySlide_1",
    "robot":    "animations/Stand/Waiting/FunnyDancer_1",
    "funny":    "animations/Stand/Waiting/FunnyDancer_1",
    "hiphop":   "animations/Stand/Waiting/FunnyDancer_1",
    "salsa":    "animations/Stand/Waiting/FunnyDancer_1",
}
_DANCE_FALLBACK = "animations/Stand/Waiting/FunnyDancer_1"

_FOLLOW_BEHAVIOR = "follow-me"


# Lookup table for the play_animation tool. Each key is a logical name the
# LLM may pass (matching what the user said), each value is a list of
# candidate behavior paths in priority order. The dispatcher uses
# _run_first_available which checks getInstalledBehaviors() and runs the
# first one that's actually present, so a NAO without a particular animation
# pack just falls back to a stock animation instead of erroring.
#
# Standard NAOqi animations are present on every NAO H25 / V6 image. Some of
# the more exotic packs (extra dances, animal poses) only exist on robots
# that had them installed via Choregraphe — list them first then a stock
# fallback last.
_ANIMATION_FALLBACK = "animations/Stand/Waiting/FunnyDancer_1"

# Verified against this NAO's actual ALBehaviorManager.getInstalledBehaviors()
# output. NAO H25 ships with stock Aldebaran animations + the user has
# `taichi-dance-free` and `follow-me` Choregraphe packs. There are NO animal
# behaviors installed, so animal requests get mapped to the most evocative
# stock alternative (e.g. "elephant" -> ShowMuscles for the heavy/strong vibe,
# "rabbit" -> Shy for skittish quick movements). When the user really wants
# real animals they need to install packs from Aldebaran's app store.
_ANIMATION_MAP = {
    # Animals — no real animal animations on this robot, map to closest
    # emotional/movement equivalent so the LLM doesn't just play FunnyDancer.
    "elephant":  ["animations/Stand/Waiting/ShowMuscles_1", _ANIMATION_FALLBACK],
    "monkey":    ["animations/Stand/Emotions/Positive/Mocker_1", _ANIMATION_FALLBACK],
    "dragon":    ["animations/Stand/Emotions/Negative/Angry_3", _ANIMATION_FALLBACK],
    "rabbit":    ["animations/Stand/Emotions/Positive/Shy_1", _ANIMATION_FALLBACK],
    "chicken":   ["animations/Stand/Emotions/Negative/Anxious_1", _ANIMATION_FALLBACK],
    "donkey":    ["animations/Stand/Emotions/Negative/Disappointed_1", _ANIMATION_FALLBACK],
    "bear":      ["animations/Stand/Waiting/ShowMuscles_2", _ANIMATION_FALLBACK],
    # Dances — taichi & follow-me are real installed packs.
    "taichi":    ["taichi-dance-free", _ANIMATION_FALLBACK],
    "tai-chi":   ["taichi-dance-free", _ANIMATION_FALLBACK],
    "kungfu":    ["animations/Stand/Waiting/KungFu_1", "taichi-dance-free", _ANIMATION_FALLBACK],
    "kung-fu":   ["animations/Stand/Waiting/KungFu_1", "taichi-dance-free", _ANIMATION_FALLBACK],
    "robot":     ["animations/Stand/Waiting/Robot_1", _ANIMATION_FALLBACK],
    "slide":     ["animations/Stand/Waiting/FunnySlide_1", _ANIMATION_FALLBACK],
    "funny":     [_ANIMATION_FALLBACK],
    # Positive emotions
    "happy":     ["animations/Stand/Emotions/Positive/Happy_4",
                  "animations/Stand/Emotions/Positive/Happy_1"],
    "laugh":     ["animations/Stand/Emotions/Positive/Laugh_1",
                  "animations/Stand/Emotions/Positive/Laugh_2"],
    "winner":    ["animations/Stand/Emotions/Positive/Winner_1",
                  "animations/Stand/Emotions/Positive/Winner_2"],
    "proud":     ["animations/Stand/Emotions/Positive/Proud_1",
                  "animations/Stand/Emotions/Positive/Proud_2"],
    "shy":       ["animations/Stand/Emotions/Positive/Shy_1",
                  "animations/Stand/Emotions/Positive/Shy_2"],
    "mocker":    ["animations/Stand/Emotions/Positive/Mocker_1"],
    "hungry":    ["animations/Stand/Emotions/Positive/Hungry_1"],
    "interested": ["animations/Stand/Emotions/Positive/Interested_1"],
    # Negative emotions
    "sad":       ["animations/Stand/Emotions/Negative/Sad_1",
                  "animations/Stand/Emotions/Negative/Sad_2"],
    "angry":     ["animations/Stand/Emotions/Negative/Angry_1",
                  "animations/Stand/Emotions/Negative/Angry_2",
                  "animations/Stand/Emotions/Negative/Angry_3"],
    "surprised": ["animations/Stand/Emotions/Negative/Surprise_1",
                  "animations/Stand/Emotions/Negative/Surprise_2"],
    "bored":     ["animations/Stand/Emotions/Negative/Bored_1",
                  "animations/Stand/Emotions/Negative/Bored_2"],
    "anxious":   ["animations/Stand/Emotions/Negative/Anxious_1"],
    "disappointed": ["animations/Stand/Emotions/Negative/Disappointed_1"],
    "frustrated": ["animations/Stand/Emotions/Negative/Frustrated_1"],
    "hurt":      ["animations/Stand/Emotions/Negative/Hurt_1",
                  "animations/Stand/Emotions/Negative/Hurt_2"],
    "embarrassed": ["animations/Stand/Emotions/Neutral/Embarrassed_1"],
    "fear":      ["animations/Stand/Emotions/Negative/Fear_1",
                  "animations/Stand/Emotions/Negative/Fear_2"],
    "fearful":   ["animations/Stand/Emotions/Negative/Fearful_1"],
    # Body talk / gestures
    "explain":     ["animations/Stand/Gestures/Explain_1",
                   "animations/Stand/Gestures/Explain_2"],
    "show_sky":    ["animations/Stand/Gestures/ShowSky_1",
                    "animations/Stand/Waiting/ShowSky_1"],
    "show_floor":  ["animations/Stand/Gestures/ShowFloor_1"],
    "show_muscle": ["animations/Stand/Waiting/ShowMuscles_1",
                    "animations/Stand/Waiting/ShowMuscles_2",
                    "animations/Stand/Emotions/Positive/Winner_1"],
    "bow":         ["animations/Stand/Gestures/BowShort_1"],
    "look_around": ["animations/Sit/Waiting/LookHand_1",
                    "animations/Sit/Waiting/LookHand_2"],
    "stretch":     ["animations/Stand/Gestures/Stretch_1",
                    "animations/Stand/Waiting/Stretch_1"],
    "rest":        ["animations/Sit/Waiting/Rest_1"],
    "drink":       ["animations/Stand/Waiting/Drink_1"],
    # Body sounds (only Sit variants are installed for these)
    "yawn":      ["animations/Sit/Waiting/Yawn_1"],
    "sneeze":    ["animations/Stand/Emotions/Neutral/Sneeze",
                  "animations/Sit/Emotions/Neutral/Sneeze_1"],
    "cough":     ["animations/Stand/Emotions/Neutral/Sneeze",
                  "animations/Sit/Emotions/Neutral/Sneeze_1"],
    "ask":       ["animations/Stand/Emotions/Neutral/AskForAttention_1",
                  "animations/Stand/Emotions/Neutral/AskForAttention_2"],
}


def _run_first_available(behav_mgr, candidates, blocking=True):
    """Try each behavior name in order; run the first one installed.

    Returns the name that ran, or None if none were installed. Avoids
    runBehavior on a missing package, which throws and shows up as an
    error in nao.log every time the LLM picks an unsupported style.
    """
    try:
        installed = set(behav_mgr.getInstalledBehaviors() or [])
    except Exception:
        installed = set()
    for cand in candidates:
        if cand in installed:
            try:
                if blocking:
                    behav_mgr.runBehavior(cand)
                else:
                    behav_mgr.startBehavior(cand)
                return cand
            except Exception as e:
                print("[nao_execute] runBehavior {0!r} failed: {1}".format(cand, e))
    print("[nao_execute] none of {0} installed".format(candidates))
    return None


# ---------------------------------------------------------------------------
# Phase 4 — body-language gesture dispatch
# ---------------------------------------------------------------------------
#
# Each gesture below runs a short ALMotion.angleInterpolation move sequence on
# a small set of joints. Durations match docs/PHASE_4_TASK_MAP.md.  All of
# them tolerate ``motion=None`` (dev/CI machine without naoqi running) by
# logging the intended call and returning — that lets the unit test in
# ``__main__`` exercise the whole path without a robot.
#
# Why this shape (helper + table) instead of an if/elif chain like the legacy
# action handlers?
#  - The 10 intents share the same callable signature and the same envelope
#    (None-guard, try/except, debug log on entry). Putting that envelope in
#    one helper means the per-gesture function only spells out the joints &
#    angles, which is what we'll iterate on as we tune the body language.
#  - A dict makes adding/removing intents a one-line change and lets the
#    server-side ``gesture`` tool validate against ``_GESTURE_TABLE.keys()``
#    when we plumb a list of supported intents back to the agents (TODO in
#    sibling worktree ``server-gesture-tool``).

_GESTURE_DEFAULT_FRACTION_MAX_SPEED = 0.3


def _log(msg):
    """Tiny print wrapper so the shape of debug output matches the rest of
    the file (``[nao_execute] ...``). Kept as a function so a future
    structured logger can replace it in one place."""
    print("[nao_execute] {0}".format(msg))


def _safe_interpolate(motion, names, angle_lists, time_lists, intent):
    """Run ``ALMotion.angleInterpolation`` defensively.

    Logs a debug line with the intent + joints either way. Returns True on
    success, False if naoqi isn't reachable or the call raises. We swallow
    any exception so a broken gesture can't take down the conversation
    loop — the worst case is "gesture didn't play".
    """
    if motion is None:
        _log("gesture[{0}] motion=None; would call angleInterpolation({1!r}, {2!r}, {3!r}, True)".format(
            intent, names, angle_lists, time_lists))
        return False
    try:
        motion.angleInterpolation(names, angle_lists, time_lists, True)
        return True
    except Exception as e:
        _log("gesture[{0}] angleInterpolation failed: {1}".format(intent, e))
        return False


def _safe_set_angles(motion, names, angles, fraction_max_speed, intent):
    """Non-blocking ``ALMotion.setAngles`` wrapper used by ``lean_in``
    and the ``breath_deep`` cycle (where we want concurrent moves)."""
    if motion is None:
        _log("gesture[{0}] motion=None; would call setAngles({1!r}, {2!r}, {3!r})".format(
            intent, names, angles, fraction_max_speed))
        return False
    try:
        motion.setAngles(names, angles, fraction_max_speed)
        return True
    except Exception as e:
        _log("gesture[{0}] setAngles failed: {1}".format(intent, e))
        return False


def _gesture_nod(motion, posture, leds, sound_localize=None):
    """2-beat affirmative head nod. Total ~600 ms (0.2 + 0.4)."""
    # HeadPitch positive = chin down on NAO H25 (NAOqi convention).
    # Sequence: rest -> chin down (+0.3) -> chin up (-0.2) -> rest. 4 keys
    # split across 0.6 s gives a snappy double-beat that reads as a clear
    # "yes" rather than a slow head-bow.
    return _safe_interpolate(
        motion,
        ["HeadPitch"],
        [[0.0, 0.3, -0.2, 0.0]],
        [[0.15, 0.30, 0.45, 0.60]],
        "nod",
    )


def _gesture_shake(motion, posture, leds, sound_localize=None):
    """Side-to-side "no" head shake. Total ~700 ms."""
    return _safe_interpolate(
        motion,
        ["HeadYaw"],
        [[0.0, 0.3, -0.3, 0.0]],
        [[0.18, 0.36, 0.54, 0.70]],
        "shake",
    )


def _gesture_lean_in(motion, posture, leds, sound_localize=None):
    """Torso forward ~5 degrees, ~1.2 s ramp.

    Spec: do NOT auto-restore — the body stays leaned-in for the duration of
    the reply. The matching ``lean_back`` (or a future ``lean_neutral`` /
    end-of-turn signal) restores it. We use ``setAngles`` rather than
    ``angleInterpolation`` so this call returns immediately and the agent
    can keep talking while the robot ramps the hip.
    """
    if motion is None:
        _log("gesture[lean_in] motion=None; would set HipPitch=0.08 over ~1.2 s")
        return False
    # fractionMaxSpeed ~ 0.07 yields ~1.2 s for the 0.08 rad excursion on a
    # NAO H25. Tune in robot trials.
    return _safe_set_angles(motion, ["HipPitch"], [0.08], 0.07, "lean_in")


def _gesture_lean_back(motion, posture, leds, sound_localize=None):
    """Torso back ~3 degrees, 800 ms. Uses angleInterpolation so the
    motion completes and returns to the LLM-controlled pose."""
    return _safe_interpolate(
        motion,
        ["HipPitch"],
        [[-0.05, 0.0]],
        [[0.50, 0.80]],
        "lean_back",
    )


def _gesture_open_arms(motion, posture, leds, sound_localize=None):
    """Both arms outward ~30 degrees. ~1 s.

    Mirrored shoulder pitch (raised) + elbow yaw (rotated outward) so the
    hands open up away from the chest. Symmetric on both arms.
    """
    return _safe_interpolate(
        motion,
        ["LShoulderPitch", "RShoulderPitch", "LElbowYaw", "RElbowYaw"],
        [
            [1.0],   # raise left shoulder (lower angle = arm up on NAO)
            [1.0],   # raise right shoulder
            [-1.4],  # left elbow rotates outward
            [1.4],   # right elbow rotates outward
        ],
        [
            [1.0],
            [1.0],
            [1.0],
            [1.0],
        ],
        "open_arms",
    )


def _gesture_point_self(motion, posture, leds, sound_localize=None):
    """Right hand to chest — "me / I". ~700 ms."""
    return _safe_interpolate(
        motion,
        ["RShoulderPitch", "RShoulderRoll", "RElbowYaw", "RElbowRoll"],
        [
            [1.2],   # shoulder slightly forward & down
            [-0.2],  # shoulder roll inward toward body
            [0.5],   # elbow yaw rotates hand inward
            [1.4],   # elbow roll bends forearm to chest
        ],
        [
            [0.70],
            [0.70],
            [0.70],
            [0.70],
        ],
        "point_self",
    )


def _gesture_point_listener(motion, posture, leds, sound_localize=None):
    """Turn head + extend right arm toward last sound source. ~900 ms.

    Queries ``sound_localize.get_last_direction()`` if a localizer was
    threaded through; falls back to azimuth=0 (straight ahead) when no
    localizer is wired up yet (sibling worktree ``robot-sound-localize``
    owns that module).
    """
    azimuth_deg = 0.0
    if sound_localize is not None:
        try:
            getter = getattr(sound_localize, "get_last_direction", None)
            if getter is not None:
                last = getter()
                if last:
                    az = last.get("azimuth_deg") if hasattr(last, "get") else None
                    if az is not None:
                        azimuth_deg = float(az)
        except Exception as e:
            _log("gesture[point_listener] sound_localize lookup failed: {0}".format(e))

    # Convert deg -> rad and clamp to a reasonable head/arm range so we
    # don't overshoot when the localizer reports a bogus angle.
    import math
    yaw_rad = max(-1.0, min(1.0, math.radians(azimuth_deg)))

    if motion is None:
        _log("gesture[point_listener] motion=None; azimuth_deg={0}; would interpolate HeadYaw + RArm".format(azimuth_deg))
        return False
    # Two parallel interpolations: head yaw lines up with the speaker, right
    # arm extends in roughly the same direction.
    return _safe_interpolate(
        motion,
        ["HeadYaw", "RShoulderPitch", "RShoulderRoll", "RElbowRoll"],
        [
            [yaw_rad],
            [0.4],            # shoulder forward & up
            [-yaw_rad - 0.2], # roll mirrors yaw (negative is outward on the right side)
            [0.05],           # elbow nearly straight
        ],
        [
            [0.90],
            [0.90],
            [0.90],
            [0.90],
        ],
        "point_listener",
    )


def _gesture_shrug(motion, posture, leds, sound_localize=None):
    """Shoulders up + slight head pitch up. ~600 ms.

    Negative ShoulderPitch on NAO = arm raised. We bump both shoulders up
    and tip the head slightly up to read as "I dunno".
    """
    return _safe_interpolate(
        motion,
        ["LShoulderPitch", "RShoulderPitch", "HeadPitch"],
        [
            [0.6, 1.5],     # raise then lower
            [0.6, 1.5],
            [-0.15, 0.0],   # head tips up then back
        ],
        [
            [0.30, 0.60],
            [0.30, 0.60],
            [0.30, 0.60],
        ],
        "shrug",
    )


def _gesture_tilt_curious(motion, posture, leds, sound_localize=None):
    """Head roll +0.21 rad (~12 deg) for a "huh?" tilt. ~500 ms."""
    return _safe_interpolate(
        motion,
        ["HeadRoll"],
        [[0.21, 0.21, 0.0]],
        [[0.20, 0.40, 0.50]],
        "tilt_curious",
    )


def _gesture_breath_deep(motion, posture, leds, sound_localize=None):
    """Slow chest-pitch breathing cycle. ~3 s.

    NAO doesn't expose a ``ChestPitch`` joint, so we simulate the breath by
    rocking the hips slightly back-and-forward + raising/lowering the
    shoulders together. Symmetric, slow, low-amplitude — no abrupt moves.
    """
    return _safe_interpolate(
        motion,
        ["HipPitch", "LShoulderPitch", "RShoulderPitch"],
        [
            [-0.04, 0.04, 0.0],    # inhale tilt back, exhale forward, settle
            [1.35, 1.55, 1.45],
            [1.35, 1.55, 1.45],
        ],
        [
            [1.20, 2.40, 3.00],
            [1.20, 2.40, 3.00],
            [1.20, 2.40, 3.00],
        ],
        "breath_deep",
    )


# Public table — one entry per canonical intent. Server-side
# ``server/tools/nao_actions.py`` validates against this set when we plumb
# the gesture tool (sibling worktree). Order is the docs canonical order.
_GESTURE_TABLE = {
    "nod":            _gesture_nod,
    "shake":          _gesture_shake,
    "lean_in":        _gesture_lean_in,
    "lean_back":      _gesture_lean_back,
    "open_arms":      _gesture_open_arms,
    "point_self":     _gesture_point_self,
    "point_listener": _gesture_point_listener,
    "shrug":          _gesture_shrug,
    "tilt_curious":   _gesture_tilt_curious,
    "breath_deep":    _gesture_breath_deep,
}


def _run_gesture(args, motion, posture, leds, sound_localize=None):
    """Look up an intent and execute the matching ``_gesture_*`` callable.

    Unknown intent -> warning + no-op (per spec). Returns True on success,
    False otherwise — never raises so the conversation loop is unaffected.
    """
    intent = (args or {}).get("intent")
    if not intent:
        _log("gesture: missing 'intent' arg; got args={0!r}".format(args))
        return False
    fn = _GESTURE_TABLE.get(intent)
    if fn is None:
        _log("gesture: unknown intent {0!r}; allowed={1}".format(
            intent, sorted(_GESTURE_TABLE.keys())))
        return False
    try:
        return bool(fn(motion, posture, leds, sound_localize=sound_localize))
    except Exception as e:
        _log("gesture[{0}] handler raised: {1}".format(intent, e))
        return False


def run(action, session, motion, posture, leds, behav_mgr, tts,
        sound_localize=None):
    """Execute a single action dict. Silently no-ops on unknown names.

    Existing 18 action tools (``stand_up`` ... ``play_animation``) keep
    their exact behavior. Phase 4 adds the ``gesture`` action — looked up
    in ``_GESTURE_TABLE``.

    ``sound_localize`` is an optional kwarg used only by ``point_listener``
    so legacy callers don't need to change.
    """
    name = action.get("name")
    args = action.get("args") or {}
    try:
        if name == "stand_up":
            posture.goToPosture("StandInit", 0.6)
        elif name == "sit_down":
            posture.goToPosture("Sit", 0.6)
        elif name == "kneel":
            posture.goToPosture("Crouch", 0.6)
        elif name == "wave_hand":
            hand = args.get("hand", "right")
            behav_mgr.runBehavior("animations/Stand/Gestures/Hey_{0}".format(
                "1" if hand == "right" else "3"))
        elif name == "wave_both_hands":
            behav_mgr.runBehavior("animations/Stand/Gestures/Hey_1")
            behav_mgr.runBehavior("animations/Stand/Gestures/Hey_3")
        elif name == "nod_head":
            n = int(args.get("times", 2))
            for _ in range(n):
                motion.angleInterpolation(["HeadPitch"], [0.3, -0.1], [0.5, 1.0], True)
        elif name == "shake_head":
            n = int(args.get("times", 2))
            for _ in range(n):
                motion.angleInterpolation(["HeadYaw"], [0.5, -0.5], [0.4, 0.8], True)
        elif name == "clap_hands":
            n = int(args.get("times", 2))
            for _ in range(n):
                behav_mgr.runBehavior("animations/Stand/Emotions/Positive/Happy_4")
        elif name == "move_forward":
            motion.moveTo(float(args.get("meters", 0.3)), 0.0, 0.0)
        elif name == "move_backward":
            motion.moveTo(-float(args.get("meters", 0.3)), 0.0, 0.0)
        elif name == "turn_left":
            import math
            motion.moveTo(0.0, 0.0, math.radians(float(args.get("degrees", 45.0))))
        elif name == "turn_right":
            import math
            motion.moveTo(0.0, 0.0, -math.radians(float(args.get("degrees", 45.0))))
        elif name == "spin":
            import math
            motion.moveTo(0.0, 0.0, math.radians(float(args.get("degrees", 360.0))))
        elif name == "dance":
            style = (args.get("style") or "robot").strip().lower()
            primary = _DANCE_BEHAVIORS.get(style)
            candidates = []
            if primary:
                candidates.append(primary)
            if _DANCE_FALLBACK not in candidates:
                candidates.append(_DANCE_FALLBACK)
            _run_first_available(behav_mgr, candidates, blocking=True)
        elif name == "change_eye_color":
            color = _EYE_COLORS.get(args.get("color", "white"), 0xFFFFFF)
            leds.fadeRGB("FaceLeds", color, 0.3)
        elif name == "follow_movement":
            # Non-blocking — follow-me runs until stopped so the user can keep
            # talking while NAO mirrors them. Stop with the stop_follow action
            # or by saying a phrase that maps to it.
            _run_first_available(
                behav_mgr,
                [_FOLLOW_BEHAVIOR, "animations/Stand/Gestures/Follow_1"],
                blocking=False,
            )
        elif name == "stop_follow":
            try: behav_mgr.stopBehavior(_FOLLOW_BEHAVIOR)
            except Exception as e: print("[nao_execute] stop_follow:", e)
        elif name == "play_animation":
            anim = (args.get("animation") or "").strip().lower()
            # Normalize a few common variants the user might say.
            anim = anim.replace(" ", "_").replace("-", "_")
            candidates = list(_ANIMATION_MAP.get(anim, []))
            # Always end with the fallback so something always plays unless
            # zero behaviors at all are installed (which would be broken).
            if _ANIMATION_FALLBACK not in candidates:
                candidates.append(_ANIMATION_FALLBACK)
            ran = _run_first_available(behav_mgr, candidates, blocking=True)
            if ran is None:
                print("[nao_execute] no animation available for {0!r}".format(anim))
        elif name == "gesture":
            _run_gesture(args, motion, posture, leds, sound_localize=sound_localize)
        else:
            print("[nao_execute] unknown action:", name)
    except Exception as e:
        print("[nao_execute] action failed:", name, "error:", e)


def dispatch(action_name, args=None, motion=None, posture=None, leds=None,
             behav_mgr=None, tts=None, session=None, sound_localize=None):
    """Phase 1+ entry point. Routes by ``action_name`` to either the
    Phase 4 gesture table or the legacy ``run()`` for the original 18 tools.

    Phase 1's ``ws_client`` and ``main.py`` both look for ``dispatch`` first
    and fall back to ``run`` if missing — defining ``dispatch`` here means
    new callers get the gesture path without anyone changing imports.
    """
    if action_name == "gesture":
        return _run_gesture(args or {}, motion, posture, leds, sound_localize=sound_localize)
    # Reuse the legacy dispatch for everything else — single source of truth
    # for the 18 existing actions. ``run`` already handles the env where any
    # of motion/posture/etc may be None on the dev box, but we still wrap it
    # in a try so an unexpected error here can't bubble up to the caller.
    try:
        return run(
            {"name": action_name, "args": args or {}},
            session, motion, posture, leds, behav_mgr, tts,
            sound_localize=sound_localize,
        )
    except Exception as e:
        _log("dispatch failed for {0!r}: {1}".format(action_name, e))
        return False


# ---------------------------------------------------------------------------
# Smoke test — run with `python nao/utils/nao_execute.py` on the dev box.
# Verifies the dispatch path doesn't raise when naoqi proxies are None and
# every gesture intent is reachable from the table.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("[nao_execute] smoke test: dispatch('gesture', {'intent': 'nod'}, motion=None) -> ", end="")
    ok = dispatch("gesture", {"intent": "nod"}, motion=None, posture=None, leds=None)
    print("returned {0!r}".format(ok))

    # Hit every gesture so the table can't silently lose an entry between
    # commits. None of these should raise.
    for _intent in sorted(_GESTURE_TABLE.keys()):
        dispatch("gesture", {"intent": _intent}, motion=None, posture=None,
                 leds=None, sound_localize=None)

    # Unknown intent path
    dispatch("gesture", {"intent": "definitely-not-a-real-intent"}, motion=None)

    # Unknown action_name -> falls through to legacy run() with no proxy
    dispatch("totally-unknown-action", {}, motion=None)

    print("[nao_execute] smoke test OK; {0} gestures registered".format(len(_GESTURE_TABLE)))
