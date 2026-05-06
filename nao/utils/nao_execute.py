# -*- coding: utf-8 -*-
"""Dispatch {name, args} records from the server to naoqi calls on NAO (Py 2.7)."""
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
# stock alternative (e.g. "elephant" → ShowMuscles for the heavy/strong vibe,
# "rabbit" → Shy for skittish quick movements). When the user really wants
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


def run(action, session, motion, posture, leds, behav_mgr, tts):
    """Execute a single action dict. Silently no-ops on unknown names."""
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
        else:
            print("[nao_execute] unknown action:", name)
    except Exception as e:
        print("[nao_execute] action failed:", name, "error:", e)
