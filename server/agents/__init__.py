"""Agent graph builders.

`nao-therapy` branch: this assistant is therapy-only. The router,
chatbot (Morgan CS), skills, and pure/embodied chat agents are
intentionally NOT imported here so they don't load at boot, but their
source files remain in `server/agents/` and `server/tools/` for
reference and to make reactivation a one-line change in any future
multi-mode branch.

Crisis gate (`server/safety.py`) is unchanged and runs pre-agent on
every turn — it bypasses the LLM entirely on hard-keyword matches.
"""
from server.agents.therapist import build_therapist_agent

# ---------------------------------------------------------------------------
# Embodiment trigger keywords are still referenced by `server/app_ws.py`
# (the pure-chat fast-fallback lane). In therapy mode that lane never
# fires (hint is never 'chat'), but we keep the symbol exported as a
# zero-op stub so the import in app_ws.py doesn't break.
# ---------------------------------------------------------------------------
_EMBODIED_TRIGGERS: tuple[str, ...] = ()


def _wants_embodied(transcript: str | None) -> bool:
    """Therapy mode never enters the pure-chat fast-fallback lane —
    every turn goes straight to the therapist agent. Return False so
    the lane selection in `app_ws.py` stays on the default `run_agent_streamed`
    path with the full agent + tools.
    """
    return False


def pick_initial_agent(username: str, hint: str | None,
                        transcript: str | None = None):
    """Return the agent to start a turn with.

    `nao-therapy` branch: always returns the therapist agent regardless
    of the legacy `hint` field. The hint is preserved on the WS frame
    contract for backward compatibility but has no effect on routing.
    Skipping the router agent saves the ~250-300 ms LLM hop that was
    previously spent just deciding to hand off to the therapist.
    """
    return build_therapist_agent(username)
