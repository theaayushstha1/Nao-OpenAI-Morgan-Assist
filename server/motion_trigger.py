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
        # Canonical phrases — fire the Choregraphe `follow-me` pack.
        # Bare "follow" omitted ("follow up on what we said" would mis-fire).
        # Bare "follow me" omitted too — it false-fires on social-media
        # mentions like "follow me on Instagram for updates". Require
        # at least one extra word ("around", "now", "come follow me").
        "come follow me", "follow me around",
        "start following me", "follow me now",
        "please follow me", "can you follow me",
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

    # ── Voice profile picker — three voices: male, female, my-clone ──
    # User can switch at any time. The write path persists the choice
    # to user_prefs.voice_profile so the next turn (and next session)
    # uses the new voice automatically.
    ("set_voice_profile", {"profile": "man"}, "Switching to my male voice.", [
        "switch to a man voice", "switch to the man voice", "switch to man voice",
        "use a man voice", "use the man voice", "use man voice",
        "male voice", "man voice", "boy voice",
        "talk like a man", "sound like a man",
    ]),
    ("set_voice_profile", {"profile": "girl"}, "Switching to my female voice.", [
        "switch to a girl voice", "switch to the girl voice", "switch to girl voice",
        "switch to a female voice", "switch to the female voice", "switch to female voice",
        "use a girl voice", "use the girl voice", "use a female voice", "use the female voice",
        "female voice", "girl voice", "woman voice",
        "talk like a girl", "sound like a girl", "talk like a woman", "sound like a woman",
    ]),
    ("set_voice_profile", {"profile": "my"}, "Switching to my cloned voice.", [
        "switch to my voice", "use my voice", "talk in my voice",
        "sound like me", "talk like me",
        "switch to the cloned voice", "use the cloned voice", "cloned voice",
        "my voice", "ayush voice",
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


# ---------------------------------------------------------------------------
# nao-therapy: face-learn fast-path with name extraction.
# ---------------------------------------------------------------------------
# Each entry is a regex with a single capture group `(name)`. The
# captured name is plumbed into the `learn_face` action's `name` arg
# and ack template. Only HIGH-CONFIDENCE patterns are listed here —
# ambiguous patterns ("I'm Aayush" can mean "I'm anxious") are left
# to the LLM which has stronger context.
#
# Name validation: must be 2-24 chars, alpha (with apostrophes /
# hyphens), and not appear in `_NON_NAMES_FAST` (common adjectives /
# nouns that follow "my name" in idioms). Names get title-cased so
# "aayush" -> "Aayush" before going to the learn_face tool.
_NON_NAMES_FAST = frozenset({
    "tired", "fine", "good", "okay", "ok", "great", "happy", "sad",
    "anxious", "stressed", "depressed", "lonely", "scared", "angry",
    "ready", "back", "here", "sorry", "done", "leaving", "going",
    "trying", "thinking",
})


def _looks_like_name(candidate: str) -> bool:
    """Conservative gate: don't fire learn_face on common adjectives."""
    c = (candidate or "").strip().lower()
    if not c or c in _NON_NAMES_FAST:
        return False
    if not (2 <= len(c) <= 24):
        return False
    # Letters, apostrophes, hyphens only (so 'O'Hara', 'Anne-Marie').
    return bool(re.match(r"^[a-z][a-z'\-]+$", c))


_LEARN_FACE_PATTERNS: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), ack_tmpl)
    for p, ack_tmpl in (
        # Only HIGH-confidence teach verbs short-circuit to learn_face.
        # The looser "my name is X" / "call me X" patterns were removed
        # because NAO's own STT echo (Deepgram transcribing its own TTS
        # playback in the room) made learn_face fire every time the
        # therapist's reply mentioned a name — producing
        # learn_face('Nao') and learn_face('Michelle') on the wrong
        # turns. Defer that inference to the therapist agent, which
        # has surrounding context and can decide whether to actually
        # call the learn_face tool.
        (r"\bremember\s+me\s+as\s+([a-z][a-z'\-]+)\b",
         "Remembering you as {name}."),
        (r"\bsave\s+my\s+face\s+as\s+([a-z][a-z'\-]+)\b",
         "Saving your face as {name}."),
        (r"\blearn\s+my\s+face\s+as\s+([a-z][a-z'\-]+)\b",
         "Learning your face as {name}."),
        (r"\bremember\s+(?:that\s+)?my\s+name\s+is\s+([a-z][a-z'\-]+)\b",
         "Remembering you as {name}."),
    )
)


def _detect_learn_face(transcript: str) -> MotionMatch | None:
    """Try to extract a name + emit a `learn_face` action without an
    LLM hop. Returns None if no high-confidence pattern matches.
    """
    for pattern, ack_tmpl in _LEARN_FACE_PATTERNS:
        m = pattern.search(transcript)
        if not m:
            continue
        raw = (m.group(1) or "").strip()
        if not _looks_like_name(raw):
            continue
        name = raw[0].upper() + raw[1:].lower()
        return MotionMatch(
            action="learn_face",
            args={"name": name},
            ack=ack_tmpl.format(name=name),
        )
    return None


def detect(transcript: str) -> MotionMatch | None:
    """Return MotionMatch if `transcript` clearly requests a NAO body action.

    Returns None for ambiguous or non-motion input — those go to the LLM.

    Order:
      1. Face-learn fast-path (regex with name capture). Highest signal,
         saves an LLM hop on the very common "remember me as X" turn.
      2. Static-phrase trigger table (`_COMPILED`). Posture, gestures,
         dances, follow-me, etc.
    """
    if not transcript:
        return None
    t = transcript.strip()
    if not t:
        return None

    # 1. learn_face fast-path.
    lf = _detect_learn_face(t)
    if lf is not None:
        return lf

    # 2. Static phrases.
    for pattern, action, args, ack in _COMPILED:
        if pattern.search(t):
            return MotionMatch(action=action, args=dict(args), ack=ack)
    return None
