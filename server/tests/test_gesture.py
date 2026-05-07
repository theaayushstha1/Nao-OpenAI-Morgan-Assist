"""Phase 4 — embodiment tests for the `gesture` tool + dispatch table.

The Phase 4 sibling worktrees (`server-gesture-tool`, `robot-gesture-dispatch`,
`robot-sound-localize`) add three new surfaces:

  - `server/tools/nao_actions.py` gains `GESTURE_INTENTS` (canonical set of
    10 intents) and a `gesture` function-tool that validates the intent and
    enqueues `{name: "gesture", args: {intent: ...}}`.
  - `server/agents/therapist.py` and `server/agents/chat.py` prompts mention
    explicit `gesture(...)` examples so the LLM actually uses the tool.
  - `nao/utils/nao_execute.py` exposes `_GESTURE_TABLE: dict[str, callable]`
    where each callable performs the per-intent ALMotion sequence.

These tests guard each of those surfaces with `pytest.importorskip`-style
checks so the suite still collects/passes even when the sibling worktrees
haven't been merged yet onto `dev/architecture-rework`.
"""
from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from server.tools import nao_actions


# ───────────────────────────── helpers ─────────────────────────────


def _has_gesture_tool() -> bool:
    return hasattr(nao_actions, "gesture") and hasattr(nao_actions, "GESTURE_INTENTS")


def _invoke_gesture(ctx: dict, intent: str) -> str:
    """Drive the `gesture` function-tool through its `on_invoke_tool` entry.

    function_tool wraps the underlying Python function in a FunctionTool whose
    primary entry point is async + JSON-keyed. We replicate the call shape the
    Agents SDK runtime would use so we exercise the actual decorated function
    (not a hand-rolled mock that would drift from real behavior).
    """
    from agents.tool_context import ToolContext
    tool = nao_actions.gesture
    tc = ToolContext(
        context=ctx,
        tool_name="gesture",
        tool_call_id="test-1",
        tool_arguments=json.dumps({"intent": intent}),
    )
    return asyncio.run(tool.on_invoke_tool(tc, json.dumps({"intent": intent})))


# ──────────────────────── tool-level tests ─────────────────────────


def test_gesture_tool_validates_intent():
    """`gesture(ctx, "nod")` queues an action, `gesture(ctx, "elephant")` is rejected."""
    if not _has_gesture_tool():
        pytest.importorskip("server.tools.nao_actions.gesture")

    ctx_ok: dict = {"actions_queue": []}
    out_ok = _invoke_gesture(ctx_ok, "nod")
    assert "nod" in out_ok
    assert ctx_ok["actions_queue"] == [
        {"name": "gesture", "args": {"intent": "nod"}}
    ]

    ctx_bad: dict = {"actions_queue": []}
    out_bad = _invoke_gesture(ctx_bad, "elephant")
    assert "unknown" in out_bad.lower()
    # Reject path must NOT enqueue anything; the LLM should fall back to
    # play_animation for novelty intents like 'elephant'.
    assert ctx_bad["actions_queue"] == []


def test_gesture_intents_canonical_set_complete():
    """All 10 canonical intents from PHASE_4_TASK_MAP must be present."""
    if not _has_gesture_tool():
        pytest.importorskip("server.tools.nao_actions.gesture")

    expected = {
        "nod", "shake", "lean_in", "lean_back", "open_arms",
        "point_self", "point_listener", "shrug", "tilt_curious", "breath_deep",
    }
    assert set(nao_actions.GESTURE_INTENTS) == expected


def test_gesture_added_to_therapist_actions():
    """`gesture` must be a member of the THERAPIST_ACTIONS bundle so the
    therapist agent has the tool wired in."""
    if not _has_gesture_tool():
        pytest.importorskip("server.tools.nao_actions.gesture")

    assert nao_actions.gesture in nao_actions.THERAPIST_ACTIONS


def test_gesture_added_to_chat_actions():
    """Same for the chat agent."""
    if not _has_gesture_tool():
        pytest.importorskip("server.tools.nao_actions.gesture")

    assert nao_actions.gesture in nao_actions.CHAT_ACTIONS


# ──────────────────────── prompt assertions ────────────────────────


def test_therapist_prompt_mentions_gesture_examples():
    """The therapist instructions must reference the `gesture(...)` tool with
    at least one concrete intent so the model knows to actually call it."""
    therapist = pytest.importorskip("server.agents.therapist")
    base = getattr(therapist, "_BASE", "")
    if "gesture" not in base.lower():
        pytest.skip("therapist prompt has not been updated with gesture examples yet")

    assert "gesture(" in base or "`gesture`" in base
    # At least one concrete intent should appear in the examples.
    intents_in_prompt = [i for i in (
        "nod", "shake", "lean_in", "open_arms", "tilt_curious",
        "point_self", "point_listener", "breath_deep", "shrug", "lean_back",
    ) if i in base]
    assert len(intents_in_prompt) >= 3, (
        "therapist prompt should mention multiple gesture intents, "
        f"found: {intents_in_prompt}"
    )


def test_chat_prompt_mentions_gesture_examples():
    """The chat instructions must reference the `gesture(...)` tool too."""
    chat = pytest.importorskip("server.agents.chat")
    system = getattr(chat, "SYSTEM", "")
    if "gesture" not in system.lower():
        pytest.skip("chat prompt has not been updated with gesture examples yet")

    assert "gesture(" in system or "`gesture`" in system
    intents_in_prompt = [i for i in (
        "nod", "shake", "lean_in", "open_arms", "tilt_curious",
        "point_self", "point_listener", "breath_deep", "shrug", "lean_back",
    ) if i in system]
    assert len(intents_in_prompt) >= 3, (
        "chat prompt should mention multiple gesture intents, "
        f"found: {intents_in_prompt}"
    )


# ──────────────────── nao-side dispatch table tests ─────────────────


def _load_gesture_table():
    """Try to import the nao-side dispatch table, skipping if not yet wired."""
    nao_execute = pytest.importorskip("nao.utils.nao_execute")
    table = getattr(nao_execute, "_GESTURE_TABLE", None)
    if not isinstance(table, dict) or not table:
        pytest.skip("_GESTURE_TABLE not present in nao.utils.nao_execute yet")
    return table


def _make_fake_proxies():
    """Build the proxy quad the dispatch callables expect.

    The task-map signature is `(motion, posture, leds, sound_localize_module)`.
    We hand back MagicMock objects so any ALMotion / ALLeds call is recorded
    without us having to enumerate every possible verb up front.
    """
    motion = MagicMock(name="ALMotion")
    posture = MagicMock(name="ALPosture")
    leds = MagicMock(name="ALLeds")
    sound_loc = MagicMock(name="sound_localize_module")
    # Default: no last direction known.
    sound_loc.get_last_direction.return_value = None
    return motion, posture, leds, sound_loc


@pytest.mark.parametrize("intent", ["nod", "lean_in", "point_listener", "breath_deep"])
def test_gesture_dispatch_invokes_motion(intent):
    """Each representative gesture must call ALMotion at least once when
    routed through `_GESTURE_TABLE[intent]`. We don't pin which API gets
    called (angleInterpolation vs setAngles vs runBehavior) because the
    sibling worktree owns that choice, but *something* on the motion proxy
    must fire — otherwise the gesture is a silent no-op on the robot."""
    table = _load_gesture_table()
    if intent not in table:
        pytest.skip(f"_GESTURE_TABLE missing intent {intent!r}")

    motion, posture, leds, sound_loc = _make_fake_proxies()
    fn = table[intent]

    # Don't over-constrain the signature — sibling impl may choose to take
    # **kwargs or positional. Try positional first, fall back to keyword.
    try:
        fn(motion, posture, leds, sound_loc)
    except TypeError:
        fn(motion=motion, posture=posture, leds=leds,
           sound_localize=sound_loc)

    # At least one ALMotion call must have happened. We accept any motion
    # verb because sibling worktree picks the best one per gesture.
    assert motion.method_calls, (
        f"gesture {intent!r} did not invoke any ALMotion API"
    )


def test_gesture_dispatch_point_listener_consults_sound_localizer():
    """`point_listener` should query the sound_localize module for the last
    direction (per PHASE_4_TASK_MAP §`nao/utils/nao_execute.py`)."""
    table = _load_gesture_table()
    if "point_listener" not in table:
        pytest.skip("_GESTURE_TABLE missing point_listener")

    motion, posture, leds, sound_loc = _make_fake_proxies()
    sound_loc.get_last_direction.return_value = {
        "azimuth_deg": 25.0, "elevation_deg": 0.0,
        "ts_ms": 0, "confidence": 0.9,
    }
    fn = table["point_listener"]
    try:
        fn(motion, posture, leds, sound_loc)
    except TypeError:
        fn(motion=motion, posture=posture, leds=leds,
           sound_localize=sound_loc)

    assert sound_loc.get_last_direction.called, (
        "point_listener gesture must consult sound_localize.get_last_direction()"
    )
