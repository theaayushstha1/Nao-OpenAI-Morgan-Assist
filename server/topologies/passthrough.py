"""Passthrough topology: existing behavior, unchanged.

Runs the agent via Runner.run exactly as server.py used to. verdict is None.
Still calls the invariant monitor so RQ2 can observe Debate/SharedPool-equivalent
unmonitored behavior (the monitor reveals violations, it cannot prevent them here).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from agents import Runner

from server import config

logger = logging.getLogger("sage.topologies.passthrough")


def _extract_user_text(message: Any) -> str:
    """Pull the user text out of either a bare string or Responses-API shape."""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        for item in message:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "input_text":
                        return c.get("text", "") or ""
            elif isinstance(content, str):
                return content
    return ""


def _record_turn_safe(
    username: str,
    user_text: str,
    proposed_reply: str,
    final_reply: str,
    verdict: dict | None,
    topology: str,
    affect: dict | None,
) -> None:
    """Best-effort call to the invariant monitor. Never raises."""
    try:
        from server.invariant import record_turn
        record_turn(
            username=username,
            user_text=user_text,
            proposed_reply=proposed_reply,
            final_reply=final_reply,
            verdict=verdict,
            topology=topology,
            affect=affect,
        )
    except Exception as e:  # noqa: BLE001 — invariant is observational, never block a turn
        logger.debug("invariant.record_turn failed (swallowed): %s", e)


def run(
    agent: Any,
    message: Any,
    *,
    context: dict,
    session: Any,
) -> tuple[str, str, dict | None, dict]:
    result = asyncio.run(Runner.run(agent, message, context=context, session=session))
    last_name = getattr(getattr(result, "last_agent", None), "name", None) or getattr(agent, "name", "agent")
    reply = result.final_output

    _record_turn_safe(
        username=context.get("username", "guest"),
        user_text=_extract_user_text(message),
        proposed_reply=reply,
        final_reply=reply,
        verdict=None,
        topology="passthrough",
        affect=context.get("affect_vector"),
    )
    return reply, last_name, None, {"topology": "passthrough"}
