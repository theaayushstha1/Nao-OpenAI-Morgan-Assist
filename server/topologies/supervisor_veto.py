"""Supervisor-Veto topology (the paper's proposed contribution).

Flow per turn:
  1. Run AffectAgent to populate context["affect_vector"].
  2. Run Runner.run(agent, ...) to get the *proposed* reply.
  3. Run SafetyAgent.evaluate(user_text, proposed_reply, affect, thought_record).
  4. If verdict == "allow"    -> emit proposed_reply.
     If verdict == "revise"   -> emit verdict["rewrite"] (fall back to proposed if empty).
     If verdict == "escalate" -> emit server.safety.HOTLINE_REPLY and set crisis_lockout.
  5. Record turn in invariant monitor.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from agents import Runner

from server import safety
from server.topologies.affect_agent import AffectAgent
from server.topologies.passthrough import _extract_user_text, _record_turn_safe
from server.topologies.safety_agent import SafetyAgent, is_escalate_category

logger = logging.getLogger("sage.topologies.supervisor_veto")


def run(
    agent: Any,
    message: Any,
    *,
    context: dict,
    session: Any,
) -> tuple[str, str, dict | None, dict]:
    user_text = _extract_user_text(message)

    # 1) Affect — safe to fail; never block the turn.
    try:
        AffectAgent().observe(context, user_text)
    except Exception as e:  # noqa: BLE001
        logger.debug("AffectAgent.observe failed (swallowed): %s", e)

    # 2) Propose a reply from the existing agent graph.
    result = asyncio.run(Runner.run(agent, message, context=context, session=session))
    proposed_reply = result.final_output
    last_name = getattr(getattr(result, "last_agent", None), "name", None) or getattr(agent, "name", "agent")

    # 3) Supervisor verdict.
    verdict = SafetyAgent().evaluate(
        user_text=user_text,
        proposed_reply=proposed_reply,
        affect_vector=context.get("affect_vector"),
        thought_record_state=context.get("thought_record"),
    )

    # 4) Apply the verdict.
    v = verdict.get("verdict", "allow")
    category = verdict.get("category", "safe")

    if v == "allow":
        final_reply = proposed_reply
    elif v == "escalate" or is_escalate_category(category):
        final_reply = safety.HOTLINE_REPLY
        context["crisis_lockout"] = True
        last_name = "safety"
    else:  # revise
        rewrite = verdict.get("rewrite")
        final_reply = rewrite if isinstance(rewrite, str) and rewrite.strip() else proposed_reply

    metadata = {
        "topology": "supervisor_veto",
        "proposed_reply": proposed_reply,
        "verdict": verdict,
    }

    _record_turn_safe(
        username=context.get("username", "guest"),
        user_text=user_text,
        proposed_reply=proposed_reply,
        final_reply=final_reply,
        verdict=verdict,
        topology="supervisor_veto",
        affect=context.get("affect_vector"),
    )
    return final_reply, last_name, verdict, metadata
