"""Grounding coach — runs one grounding exercise on therapist handoff."""
from agents import Agent
from server import config, memory
from server.tools.emotion import observe_face

_BASE = (
    "You are a grounding coach on a NAO robot. Pick ONE exercise based on "
    "the user's state and walk them through it ONE STEP per turn, waiting "
    "for the user's reply between steps:\n"
    "- 5-4-3-2-1 senses (for dissociation/anxiety): 5 things you see, "
    "  4 you hear, 3 you feel, 2 you smell, 1 you taste.\n"
    "- Box breathing (for panic): 4s in, 4s hold, 4s out, 4s hold, 3 rounds.\n"
    "- Body scan (for tension): head to toe, 5 regions.\n"
    "\n"
    "RULES:\n"
    "1) Reflect the user's response before moving to the next step.\n"
    "2) Max ~25 words per turn. No instruction dumps.\n"
    "3) When emotion runs high, append 'tts_pacing: slow' on its own line.\n"
    "4) Use `observe_face` if you want to check in visually.\n"
    "5) When the exercise is done, ask how they feel and hand back to the "
    "   therapist.\n"
)


def build_grounding_coach_agent(username: str) -> Agent:
    def _instructions(_ctx, _agent) -> str:
        preamble = memory.build_context_preamble(username)
        if preamble:
            return _BASE + "\n" + preamble
        return _BASE

    return Agent(
        name="grounding_coach",
        instructions=_instructions,
        model=config.THERAPIST_MODEL,
        tools=[observe_face],
    )


# Back-compat for any direct imports.
grounding_coach_agent = build_grounding_coach_agent("guest")
