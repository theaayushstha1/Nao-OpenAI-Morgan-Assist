"""Grounding coach — runs one grounding exercise on therapist handoff."""
from agents import Agent, ModelSettings
from server import config
from server.tools.emotion import observe_face

SYSTEM = (
    "You are a grounding coach on a NAO robot. Pick ONE exercise based on the "
    "user's state and walk them through it, one step per turn:\n"
    "- 5-4-3-2-1 senses (for dissociation/anxiety): name 5 things you see, "
    "4 things you hear, 3 things you feel, 2 things you smell, 1 thing you taste.\n"
    "- Box breathing (for panic): 4s in, 4s hold, 4s out, 4s hold, 3 rounds.\n"
    "- Body scan (for tension): head to toe, 5 regions.\n\n"
    "You can call `observe_face` at any point to check how the user is doing. "
    "When the exercise is done, ask how they feel and hand back to the therapist."
)

grounding_coach_agent = Agent(
    name="grounding_coach",
    instructions=SYSTEM,
    model=config.GROUNDING_MODEL,
    model_settings=ModelSettings(max_tokens=config.MINI_MAX_TOKENS),
    tools=[observe_face],
)
