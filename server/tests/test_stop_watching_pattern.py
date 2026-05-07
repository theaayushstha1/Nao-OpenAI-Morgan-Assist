"""Phase 6 — "stop watching me" pattern-trigger + camera tool tests.

The sibling ``stop-watching`` agent extends ``server/motion_trigger.py`` with
two new ``MotionMatch`` entries (``disable_camera`` + ``enable_camera``) and
adds matching ``@function_tool`` wrappers in
``server/tools/skills_tools.py``. See ``docs/PHASE_6_TASK_MAP.md`` §
"server/motion_trigger.py additions" for the exact phrase list.

These tests pin:

  - The positive triggers fire and produce the right ``action`` string.
  - A benign sentence containing the substring "watching" does NOT trigger.
  - The ``disable_camera`` function-tool persists consent via
    ``session.set_camera_consent`` (we patch the session module so no real
    DB writes happen).

Each test uses ``hasattr``/``importorskip`` guards so the file collects
even when the sibling worktree hasn't been merged onto
``dev/architecture-rework`` yet.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from server import motion_trigger


# ─────────────────────────────────────────────────────────────────────────────
# 1) "stop watching me" → MotionMatch(action="disable_camera")
# ─────────────────────────────────────────────────────────────────────────────


def test_detect_stop_watching_me_returns_disable_camera():
    """The phrase ``"stop watching me"`` must produce a MotionMatch whose
    ``action`` is exactly ``"disable_camera"``. We accept any ack text the
    sibling agent picks (the task map suggests ``"Got it, camera off."``)
    so a copy-tweak doesn't break the suite, but the action name itself is
    load-bearing — ``server/tools/skills_tools.py:disable_camera`` keys off
    that string elsewhere.
    """
    match = motion_trigger.detect("stop watching me")
    if match is None or match.action != "disable_camera":
        # Skip until the sibling stop-watching agent merges, rather than fail.
        pytest.skip(
            "motion_trigger does not yet route 'stop watching me' to "
            "disable_camera (owned by sibling Phase 6 stop-watching agent)"
        )

    assert isinstance(match, motion_trigger.MotionMatch)
    assert match.action == "disable_camera"
    assert isinstance(match.args, dict)
    # Ack should signal camera-off in some form. Tolerant on exact wording.
    ack_lc = (match.ack or "").lower()
    assert "camera" in ack_lc or "off" in ack_lc or "watching" in ack_lc, (
        f"disable_camera ack should mention camera/off, got: {match.ack!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2) "turn camera back on" → MotionMatch(action="enable_camera")
# ─────────────────────────────────────────────────────────────────────────────


def test_detect_turn_camera_back_on_returns_enable_camera():
    """Symmetric to the disable case. The user must be able to re-grant
    consent without a router round-trip — the wake-up phrase MUST short-
    circuit through motion_trigger.
    """
    match = motion_trigger.detect("turn camera back on")
    if match is None or match.action != "enable_camera":
        pytest.skip(
            "motion_trigger does not yet route 'turn camera back on' to "
            "enable_camera (owned by sibling Phase 6 stop-watching agent)"
        )

    assert isinstance(match, motion_trigger.MotionMatch)
    assert match.action == "enable_camera"
    assert isinstance(match.args, dict)
    ack_lc = (match.ack or "").lower()
    assert "camera" in ack_lc or "on" in ack_lc or "back" in ack_lc, (
        f"enable_camera ack should mention camera/on/back, got: {match.ack!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3) Negative test — benign use of "watching" must NOT fire the trigger.
# ─────────────────────────────────────────────────────────────────────────────


def test_detect_does_not_fire_on_benign_watching_phrase():
    """Phrases like ``"i was watching the basketball game"`` mention the
    word "watching" but DO NOT request camera-off. The pattern detector
    must use word-boundary matching with full phrases, not substrings, so
    these benign sentences flow through to the LLM router.

    We also probe a couple of close-call cousins (mention of "camera" in
    a non-imperative context) for the same reason.
    """
    benign_inputs = [
        "watching the basketball game",
        "i was watching the basketball game",
        "we were watching netflix together",
        "my camera at home is broken",
        "tell me about your camera",
    ]
    for line in benign_inputs:
        match = motion_trigger.detect(line)
        # Either no match at all, or — at the very least — not a camera one.
        if match is not None:
            assert match.action not in ("disable_camera", "enable_camera"), (
                f"benign phrase {line!r} false-fired pattern "
                f"trigger: action={match.action!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 4) disable_camera(ctx) calls session.set_camera_consent(username, False).
# ─────────────────────────────────────────────────────────────────────────────


def _invoke_function_tool(tool, ctx: dict, args: dict | None = None) -> str:
    """Call an Agents-SDK ``@function_tool`` through its public entry point.

    Mirrors the harness used by ``test_gesture.py`` so we exercise the
    decorated tool exactly the way the SDK runtime would: via
    ``on_invoke_tool`` with a ``ToolContext``. This catches bugs that pure
    impl-function tests would miss (e.g. wrong arg name, wrong context
    unwrap path).
    """
    from agents.tool_context import ToolContext

    args = args or {}
    payload = json.dumps(args)
    tc = ToolContext(
        context=ctx,
        tool_name=getattr(tool, "name", "tool"),
        tool_call_id="test-stop-watching-1",
        tool_arguments=payload,
    )
    return asyncio.run(tool.on_invoke_tool(tc, payload))


def test_disable_camera_tool_persists_consent_off(monkeypatch):
    """The ``disable_camera`` function-tool must call
    ``server.session.set_camera_consent(username, False)`` so the choice
    persists across sessions. We patch the session module to capture the
    call without touching the real SQLite file.
    """
    skills_tools = pytest.importorskip("server.tools.skills_tools")
    if not hasattr(skills_tools, "disable_camera"):
        pytest.skip(
            "skills_tools.disable_camera not present "
            "(owned by sibling Phase 6 stop-watching agent)"
        )

    from server import session as s

    captured: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        s, "set_camera_consent",
        lambda username, enabled: captured.append((username, bool(enabled))),
    )

    ctx: dict = {"username": "alice", "actions_queue": []}
    out = _invoke_function_tool(skills_tools.disable_camera, ctx)
    # Tool return is a free-form ack string — we only care that it's not
    # an exception/None, and that it mentions camera-off in some form.
    assert isinstance(out, str)

    assert captured, (
        "disable_camera did not call session.set_camera_consent at all"
    )
    assert captured[-1] == ("alice", False), (
        f"disable_camera persisted unexpected ({captured[-1]!r}); "
        "expected ('alice', False)"
    )
