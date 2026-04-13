# -*- coding: utf-8 -*-
"""Dispatch {name, args} records from the server to naoqi calls on NAO (Py 2.7)."""
from __future__ import print_function


_EYE_COLORS = {
    "red": 0xFF0000, "green": 0x00FF00, "blue": 0x0000FF,
    "yellow": 0xFFFF00, "purple": 0x800080, "white": 0xFFFFFF,
}


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
            style = args.get("style", "robot")
            behav_mgr.runBehavior("dance-{0}/behavior_1".format(style))
        elif name == "change_eye_color":
            color = _EYE_COLORS.get(args.get("color", "white"), 0xFFFFFF)
            leds.fadeRGB("FaceLeds", color, 0.3)
        elif name == "follow_movement":
            pass
        else:
            print("[nao_execute] unknown action:", name)
    except Exception as e:
        print("[nao_execute] action failed:", name, "error:", e)
