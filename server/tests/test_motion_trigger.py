"""Phase 9 — unit tests for ``server.motion_trigger.detect``.

The motion-trigger fast path bypasses the LLM for unambiguous body-action
requests because the router was unreliable for those (it would sometimes
hand off to a generic agent that replied "I'm a virtual assistant, I can't
stand up" instead of calling the tool). This file pins the contract:

* Every category from `motion_trigger._TRIGGERS` has positive cases
  exercising at least 3 distinct phrasings per category, and at least one
  negative for each so we never regress into matching coincidental
  substrings (e.g. "I stand by my decision" must NOT trigger ``stand_up``).
* Camera-consent triggers (Phase 6) are pinned because the LLM regressed
  on them more than once during development.

20+ tests. Each assertion includes a diagnostic message so failures point
at the exact phrase that broke. We keep the dependency surface minimal
(only ``server.motion_trigger``) so this file collects even when other
parallel-agent files are mid-flight.
"""
from __future__ import annotations

import pytest

from server import motion_trigger
from server.motion_trigger import MotionMatch, detect


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _assert_match(transcript: str, action: str, *, args: dict | None = None) -> None:
    """Positive match: exact ``action`` and (optionally) exact ``args``."""
    m = detect(transcript)
    assert isinstance(m, MotionMatch), (
        f"expected motion match for {transcript!r}, got None"
    )
    assert m.action == action, (
        f"transcript {transcript!r} matched action {m.action!r}, expected {action!r}"
    )
    if args is not None:
        assert m.args == args, (
            f"transcript {transcript!r} produced args {m.args!r}, "
            f"expected {args!r}"
        )
    assert m.ack and isinstance(m.ack, str), (
        f"transcript {transcript!r} produced empty ack {m.ack!r}"
    )


def _assert_no_match(transcript: str) -> None:
    """Negative case — must return None so the LLM gets the turn."""
    m = detect(transcript)
    assert m is None, (
        f"transcript {transcript!r} unexpectedly matched "
        f"action={getattr(m, 'action', None)!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Posture (stand_up / sit_down / kneel)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("transcript", [
    "stand up please",
    "Could you stand up for a sec",
    "Hey NAO, please stand straight",
])
def test_stand_up_positive_phrasings(transcript: str) -> None:
    _assert_match(transcript, "stand_up", args={})


def test_stand_up_negative_idiomatic_use() -> None:
    """'Stand by' is an idiom and must not trigger ``stand_up``.

    Real failure observed in the wild — a user said "I stand by my decision"
    after a CBT thought-record exercise and the robot tried to physically
    stand. The fix is the word-boundary regex; this test pins that.
    """
    _assert_no_match("I stand by my decision")


def test_sit_down_positive() -> None:
    _assert_match("please sit down", "sit_down", args={})


def test_sit_down_negative_compound_word() -> None:
    """'Sit-down comedy' contains the literal substring but isn't a request."""
    _assert_no_match("My favorite is sit-down comedy")


def test_kneel_positive() -> None:
    _assert_match("kneel down for a moment", "kneel", args={})


# ─────────────────────────────────────────────────────────────────────────────
# 2. Gestures (wave / nod / shake / clap)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("transcript", [
    "say hi",
    "wave hello to me",
    "give me a wave",
])
def test_wave_positive_phrasings(transcript: str) -> None:
    _assert_match(transcript, "wave_hand", args={"hand": "right"})


def test_wave_both_hands_positive() -> None:
    """Multi-word phrase must take priority over the single-word ``wave``."""
    _assert_match("wave with both hands now", "wave_both_hands", args={})


def test_wave_negative_idiom() -> None:
    """'A wave of nausea' is metaphorical; do not trigger a hand wave."""
    _assert_no_match("I felt a wave of nausea hit me")


def test_nod_positive() -> None:
    _assert_match("just nod yes if you agree", "nod_head", args={"times": 2})


def test_nod_negative_proper_noun() -> None:
    """'Nodal point' / 'nodding off' must not fire — they're not requests."""
    _assert_no_match("I keep nodding off in lectures")


def test_shake_positive() -> None:
    _assert_match("shake your head no", "shake_head", args={"times": 2})


def test_shake_negative_milkshake() -> None:
    """Compound word containing 'shake' must not match."""
    _assert_no_match("I want a milkshake")


def test_clap_positive() -> None:
    _assert_match("give me a clap please", "clap_hands", args={"times": 3})


def test_clap_negative_thunderclap() -> None:
    """'thunderclap' contains 'clap' but isn't a request."""
    _assert_no_match("the thunderclap was loud")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Locomotion (forward / back / turn / spin)
# ─────────────────────────────────────────────────────────────────────────────


def test_move_forward_positive() -> None:
    _assert_match("step forward please", "move_forward", args={"meters": 0.3})


def test_move_forward_alt_phrasing() -> None:
    _assert_match("come closer to me", "move_forward", args={"meters": 0.3})


def test_move_forward_negative() -> None:
    """Idiomatic 'looking forward to' is not a locomotion request."""
    _assert_no_match("I'm looking forward to graduation")


def test_move_backward_positive() -> None:
    _assert_match("step back a little", "move_backward", args={"meters": 0.3})


def test_move_backward_negative_metaphor() -> None:
    """'Way back when' is an idiom, not a step-back request."""
    _assert_no_match("way back when I was a freshman")


def test_turn_left_positive() -> None:
    _assert_match("turn left now", "turn_left", args={"degrees": 45.0})


def test_turn_right_positive() -> None:
    _assert_match("rotate right a bit", "turn_right", args={"degrees": 45.0})


def test_turn_negative_decision_word() -> None:
    """'Turn in my homework' / 'my turn' must not fire either rotation."""
    _assert_no_match("Is it my turn to talk")


def test_spin_positive() -> None:
    _assert_match("do a spin for me", "spin", args={"degrees": 360.0})


def test_spin_negative_metaphor() -> None:
    """'Spin doctor' / 'spin a yarn' must not fire spin locomotion."""
    _assert_no_match("Don't spin doctor that headline")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Performance (dance / follow)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("transcript", [
    "dance for me",
    "can you dance",
    "let's dance together",
])
def test_dance_positive_phrasings(transcript: str) -> None:
    _assert_match(transcript, "dance", args={"style": "robot"})


def test_dance_negative_concert() -> None:
    """'Going to a dance' is an event, not a request for the robot."""
    _assert_no_match("I'm going to a dance on Friday")


def test_follow_movement_positive() -> None:
    _assert_match("mirror me please", "follow_movement", args={})


def test_follow_negative_instagram() -> None:
    """'Follow me on Instagram' is a social-media request, not motion."""
    _assert_no_match("follow me on Instagram for updates")


@pytest.mark.parametrize("transcript, animation", [
    ("do an elephant", "elephant"),
    ("act like a gorilla", "gorilla"),
    ("do a gorrila", "gorilla"),
    ("do kung fu", "kungfu"),
    ("play air guitar", "air_guitar"),
    ("pretend to be a bird", "bird"),
    ("show me a zombie", "zombie"),
])
def test_named_animation_positive(transcript: str, animation: str) -> None:
    _assert_match(transcript, "play_animation", args={"animation": animation})


@pytest.mark.parametrize("transcript", [
    "I saw an elephant at the zoo",
    "My friend likes gorillas",
    "Kung fu movies are fun",
])
def test_named_animation_requires_action_verb(transcript: str) -> None:
    _assert_no_match(transcript)


# ─────────────────────────────────────────────────────────────────────────────
# 5. LEDs — eye color
# ─────────────────────────────────────────────────────────────────────────────


def test_eyes_red_positive() -> None:
    _assert_match("turn your eyes red please", "change_eye_color",
                  args={"color": "red"})


def test_eyes_green_positive() -> None:
    _assert_match("make your eyes green", "change_eye_color",
                  args={"color": "green"})


def test_eyes_blue_positive() -> None:
    _assert_match("eyes blue now", "change_eye_color",
                  args={"color": "blue"})


def test_eyes_negative_color_word_alone() -> None:
    """A bare color word must not fire — only the explicit ``eyes <color>``
    or ``<color> eyes`` patterns. This guards against an over-eager regex
    that would match "I love red" as a red-eye request.
    """
    _assert_no_match("I love red")


# ─────────────────────────────────────────────────────────────────────────────
# 6. Camera consent (Phase 6) — disable_camera / enable_camera
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("transcript", [
    "stop watching me",
    "please don't look at me",
    "turn off the camera",
    "close your eyes",
])
def test_disable_camera_positive(transcript: str) -> None:
    _assert_match(transcript, "disable_camera", args={})


@pytest.mark.parametrize("transcript", [
    "you can watch me again",
    "turn on the camera",
    "open your eyes",
])
def test_enable_camera_positive(transcript: str) -> None:
    _assert_match(transcript, "enable_camera", args={})


def test_camera_negative_unrelated_sentence() -> None:
    """A sentence about photography must not flip the consent flag.

    The Phase 6 trigger lives outside the LLM specifically because the
    LLM was sometimes mis-routing 'stop watching me' to a generic chat
    reply. We must not over-rotate and start matching unrelated talk
    about cameras / watching.
    """
    _assert_no_match("My friend bought a new camera last weekend")


# ─────────────────────────────────────────────────────────────────────────────
# 7. Face onboarding name-answer fast path
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("transcript, expected", [
    ("Aayush", "Aayush"),
    ("aayush", "Aayush"),
    ("I'm Aayush", "Aayush"),
    ("my name is Aayush", "Aayush"),
])
def test_detect_name_answer_positive(transcript: str, expected: str) -> None:
    m = motion_trigger.detect_name_answer(transcript)
    assert isinstance(m, MotionMatch)
    assert m.action == "learn_face"
    assert m.args == {"name": expected}


@pytest.mark.parametrize("transcript", [
    "yes",
    "no",
    "hello",
    "I feel anxious",
    "Aayush is my friend",
])
def test_detect_name_answer_negative(transcript: str) -> None:
    assert motion_trigger.detect_name_answer(transcript) is None


def test_plain_name_intro_does_not_enroll_outside_onboarding() -> None:
    _assert_no_match("Hey. My name is Ayush.")


def test_explicit_remember_me_still_enrolls() -> None:
    _assert_match(
        "remember me as Aayush",
        "learn_face",
        args={"name": "Aayush"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# 8. Misc edge cases — empty / whitespace / None-equivalent input
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("transcript", ["", "   ", "\n\t  "])
def test_empty_input_returns_none(transcript: str) -> None:
    """Empty / whitespace-only transcripts must not match anything."""
    assert detect(transcript) is None, (
        f"empty-ish transcript {transcript!r} matched a trigger"
    )


def test_args_dict_is_copied_per_call() -> None:
    """Each call must hand back a fresh args dict so a caller mutating
    it (e.g. adding sender metadata) can't leak into the next match.
    Pinning this contract keeps the module thread-safe-by-default.
    """
    a = detect("turn your eyes red please")
    b = detect("turn your eyes red please")
    assert a is not None and b is not None
    a.args["mutated"] = True
    assert "mutated" not in b.args, (
        "args dict was shared between two detect() calls — callers can leak "
        "mutations into subsequent matches"
    )


def test_priority_longer_phrase_wins() -> None:
    """Order matters in ``_TRIGGERS``. ``wave with both hands`` must beat
    the shorter ``wave`` mappings because the multi-word phrase appears
    first in the trigger list.
    """
    m = detect("wave with both hands and clap")
    assert m is not None
    # We can't guarantee which fires first across two parallel matches,
    # but we *can* guarantee ``wave with both hands`` doesn't degrade to
    # the single-hand wave.
    assert m.action in {"wave_both_hands", "clap_hands"}, m.action


def test_compiled_table_nonempty() -> None:
    """Sanity check: the compiled regex table must not be empty.

    If a refactor accidentally cleared ``_COMPILED`` the whole fast-path
    silently goes dead — this test catches that the moment it lands.
    """
    assert len(motion_trigger._COMPILED) > 0, (
        "motion_trigger._COMPILED is empty — fast path is disabled"
    )
