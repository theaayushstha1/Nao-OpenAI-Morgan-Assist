"""Pattern-based motion intent detector.

Bypasses the LLM for unambiguous body-action requests. The router was
unreliable here — it would sometimes hand off to a generic agent that
replied "I'm a virtual assistant, I can't stand up" instead of calling
the tool. This module catches those transcripts BEFORE the agent runs
and emits the action + a short ack directly.

Order matters: longer, more specific phrases come first so that
"sit down" doesn't match the "sit" inside "sit-down comedy".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# (action_name, args_dict, ack_text, list_of_phrases)
# Phrases are matched as case-insensitive whole-word substrings of the
# transcript. Order top-to-bottom = priority.
_TRIGGERS: list[tuple[str, dict, str, list[str]]] = [
    # ── Posture ─────────────────────────────────────────────
    ("stand_up", {}, "Standing up.", [
        "stand up", "get up", "stand straight", "rise up", "to your feet",
        "stand please", "please stand", "could you stand", "can you stand",
    ]),
    ("sit_down", {}, "Sitting down.", [
        "sit down", "have a seat", "take a seat", "please sit", "could you sit",
    ]),
    ("kneel", {}, "Kneeling.", [
        "kneel down", "kneel", "go on one knee",
    ]),

    # ── Gestures ────────────────────────────────────────────
    ("wave_both_hands", {}, "Waving with both hands!", [
        "wave with both hands", "wave both hands", "wave both",
    ]),
    ("wave_hand", {"hand": "right"}, "Waving hi!", [
        "wave hi", "wave hello", "say hi", "say hello", "wave hand",
        "wave at me", "give me a wave", "wave please", "could you wave",
        "can you wave", "just wave",
    ]),
    ("nod_head", {"times": 2}, "*nods*", [
        "nod your head", "nod twice", "nod yes", "give me a nod", "just nod",
    ]),
    ("shake_head", {"times": 2}, "*shakes head*", [
        "shake your head", "shake head", "say no with your head",
    ]),
    ("clap_hands", {"times": 3}, "*claps*", [
        "clap your hands", "clap for me", "give me a clap", "applaud",
        "round of applause",
    ]),

    # ── Locomotion ──────────────────────────────────────────
    ("move_forward", {"meters": 0.3}, "Walking forward.", [
        "step forward", "walk forward", "come forward", "move forward",
        "come closer",
    ]),
    ("move_backward", {"meters": 0.3}, "Stepping back.", [
        "step back", "walk back", "move back", "step backward", "back up",
    ]),
    ("turn_left", {"degrees": 45.0}, "Turning left.", [
        "turn left", "rotate left", "look left",
    ]),
    ("turn_right", {"degrees": 45.0}, "Turning right.", [
        "turn right", "rotate right", "look right",
    ]),
    ("spin", {"degrees": 360.0}, "Spinning!", [
        "spin around", "do a spin", "twirl", "full turn",
    ]),

    # ── Performance ─────────────────────────────────────────
    ("dance", {"style": "robot"}, "Let's dance!", [
        "do a dance", "show me a dance", "dance for me", "dance please",
        "give me a dance", "can you dance", "could you dance", "let's dance",
        "do a robot dance", "do the robot",
    ]),
    ("follow_movement", {}, "Following you now.", [
        # Canonical short form — fires the Choregraphe `follow-me` pack.
        # Bare "follow" alone is omitted on purpose — too greedy
        # ("follow up on what we said" would mis-fire).
        "follow me", "come follow me", "follow me around",
        "start following me", "follow me now",
        # Tracking semantics (these are unambiguous robot commands)
        "track me", "stay close", "stay with me",
        # Mirror-me semantics (legacy — copies user's pose)
        "follow my movement", "mirror me", "copy me", "follow what i do",
    ]),
    # Stop the follow behavior. NOTE: avoid single-word "stop" / "halt"
    # here because triggers match on word boundaries, and a bare "stop"
    # would mis-fire on phrases like "stop watching me" (camera-off
    # trigger) or "stop talking". Use multi-word phrases only.
    ("stop_follow", {}, "Stopping.", [
        "stop following me", "stop following", "don't follow me",
        "do not follow me", "stop tracking me", "stop tracking",
        "stay there", "stay here", "stay still", "freeze",
        "enough following",
    ]),

    # ── Camera consent ──────────────────────────────────────
    # Action names are server-side identifiers, not NAO motor calls. The
    # consumer (app_ws.py) flips session.set_camera_consent(...) and emits a
    # `control { subtype: "camera_state", data: {enabled: ...} }` frame so
    # the client UI updates immediately. The fast path here exists because
    # the LLM sometimes mis-routes "stop watching me" to a generic chat
    # reply instead of calling the tool — a regex match guarantees the
    # state flip and the canonical ack land on the same turn.
    ("disable_camera", {}, "Camera off.", [
        "stop watching me", "stop watching", "don't watch me", "do not watch me",
        "stop looking at me", "don't look at me", "do not look at me",
        "turn off the camera", "turn the camera off", "camera off",
        "disable the camera", "disable camera", "close your eyes",
        "stop recording me", "stop seeing me",
    ]),
    ("enable_camera", {}, "Camera on.", [
        "you can watch me again", "you can look at me again", "watch me again",
        "look at me again", "turn on the camera", "turn the camera on",
        "camera on", "enable the camera", "enable camera", "open your eyes",
        "you can see me now", "see me again",
    ]),

    # ── Voice profile picker (Phase 11.8) ───────────────────
    # Three voices: girl, man, neutral. Recognized via short phrases the
    # user can say at any time during a session. The handler in app_ws
    # reads `args.profile` and persists via session.set_voice_profile.
    ("set_voice_profile", {"profile": "girl"}, "Switching to girl voice.", [
        "use the girl voice", "use girl voice", "girl voice",
        "switch to girl voice", "switch to the girl voice",
        "use a woman's voice", "use the woman voice", "female voice",
        "use voice one", "voice one", "voice 1", "first voice",
    ]),
    ("set_voice_profile", {"profile": "man"}, "Switching to man voice.", [
        "use the man voice", "use man voice", "man voice",
        "switch to man voice", "switch to the man voice",
        "use a man's voice", "use a male voice", "male voice", "guy voice",
        "use voice two", "voice two", "voice 2", "second voice",
    ]),
    ("set_voice_profile", {"profile": "neutral"}, "Switching to neutral voice.", [
        "use the neutral voice", "use neutral voice", "neutral voice",
        "switch to neutral voice", "switch to the neutral voice",
        "use voice three", "voice three", "voice 3", "third voice",
    ]),
    ("set_voice_profile", {"profile": "my"}, "Switching to your voice.", [
        "switch to my voice", "use my voice", "my voice",
        "switch to your voice", "use your voice", "your voice",
        "switch to aayush voice", "aayush voice", "use aayush voice",
        "switch to operator voice", "operator voice",
        "use voice four", "voice four", "voice 4", "fourth voice",
    ]),

    # ── LEDs ────────────────────────────────────────────────
    ("change_eye_color", {"color": "red"}, "Eyes red.", [
        "eyes red", "red eyes", "turn your eyes red", "make your eyes red",
    ]),
    ("change_eye_color", {"color": "green"}, "Eyes green.", [
        "eyes green", "green eyes", "turn your eyes green", "make your eyes green",
    ]),
    ("change_eye_color", {"color": "blue"}, "Eyes blue.", [
        "eyes blue", "blue eyes", "turn your eyes blue", "make your eyes blue",
    ]),
    ("change_eye_color", {"color": "purple"}, "Eyes purple.", [
        "eyes purple", "purple eyes",
    ]),
    ("change_eye_color", {"color": "yellow"}, "Eyes yellow.", [
        "eyes yellow", "yellow eyes",
    ]),
    ("change_eye_color", {"color": "white"}, "Eyes white.", [
        "eyes white", "white eyes", "reset your eyes", "default eyes",
    ]),
]

# Pre-compile a list of (compiled_regex, action_name, args, ack)
_COMPILED: list[tuple[re.Pattern, str, dict, str]] = []
for action, args, ack, phrases in _TRIGGERS:
    for p in phrases:
        # Word-boundary match around the phrase. Allows "please stand up now"
        # to match "stand up" but not "withstand uphill".
        pattern = re.compile(r"\b" + re.escape(p) + r"\b", re.IGNORECASE)
        _COMPILED.append((pattern, action, args, ack))


@dataclass
class MotionMatch:
    action: str
    args: dict
    ack: str


def detect(transcript: str) -> MotionMatch | None:
    """Return MotionMatch if `transcript` clearly requests a NAO body action.

    Returns None for ambiguous or non-motion input — those go to the LLM.
    """
    if not transcript:
        return None
    t = transcript.strip()
    if not t:
        return None
    for pattern, action, args, ack in _COMPILED:
        if pattern.search(t):
            return MotionMatch(action=action, args=dict(args), ack=ack)
    return None
