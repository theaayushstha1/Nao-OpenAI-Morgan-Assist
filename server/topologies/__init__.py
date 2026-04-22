"""SAGE-CBT topology dispatcher.

Public contract:
    run_topology(agent, message, *, context, session)
        -> (reply: str, last_agent_name: str, verdict: dict | None, metadata: dict)

The dispatcher selects an implementation based on `server.config.SAGE_TOPOLOGY`:

    - "passthrough"       -> passthrough.run    (default; existing behavior)
    - "supervisor_veto"   -> supervisor_veto.run
    - "debate"            -> debate.run
    - "shared_pool"       -> shared_pool.run

Every impl is responsible for:
  * calling Runner.run (sync via asyncio.run — callers stay non-async)
  * returning the 4-tuple above
  * calling server.invariant.record_turn at end-of-turn (best-effort, never raises)

If SAGE_TOPOLOGY is unknown, we fall back to passthrough and log the fact.
"""
from __future__ import annotations

import logging
from typing import Any

from server import config

logger = logging.getLogger("sage.topologies")


def run_topology(
    agent: Any,
    message: Any,
    *,
    context: dict,
    session: Any,
) -> tuple[str, str, dict | None, dict]:
    """Dispatch to the topology implementation named by config.SAGE_TOPOLOGY."""
    topology = (config.SAGE_TOPOLOGY or "passthrough").strip().lower()

    if topology == "passthrough":
        from server.topologies import passthrough
        return passthrough.run(agent, message, context=context, session=session)
    if topology == "supervisor_veto":
        from server.topologies import supervisor_veto
        return supervisor_veto.run(agent, message, context=context, session=session)
    if topology == "debate":
        from server.topologies import debate
        return debate.run(agent, message, context=context, session=session)
    if topology == "shared_pool":
        from server.topologies import shared_pool
        return shared_pool.run(agent, message, context=context, session=session)

    logger.warning(
        "unknown SAGE_TOPOLOGY=%r; falling back to passthrough", topology
    )
    from server.topologies import passthrough
    return passthrough.run(agent, message, context=context, session=session)


__all__ = ["run_topology"]
