"""Helper for injecting per-face memory preamble into agent instructions.

Used by the singleton agents (chat, chatbot, skills) and the router so that
returning users get the same context awareness the therapy agents already
have. The therapy agents build a fresh agent per turn with the username
captured in closure; the singletons read it from RunContextWrapper instead.
"""
from __future__ import annotations

from typing import Callable

from server import memory


def with_memory_preamble(base_instructions: str) -> Callable:
    """Return an instructions callable that prepends the user's memory
    preamble to a static system prompt. Falls back to the base prompt for
    new users (preamble is empty)."""

    def _instructions(ctx, _agent) -> str:
        store = getattr(ctx, "context", None) or {}
        username = store.get("username", "guest")
        preamble = memory.build_context_preamble(username)
        if not preamble:
            return base_instructions
        return base_instructions + "\n\n" + preamble

    return _instructions
