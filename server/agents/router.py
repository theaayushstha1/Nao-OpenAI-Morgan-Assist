"""Router — triage agent that picks a specialist."""
from agents import Agent, ModelSettings, handoff
from server import config
from server.agents._memory_inject import with_memory_preamble
from server.agents.chat import chat_agent
from server.agents.chatbot import chatbot_agent
from server.agents.skills import skills_agent
from server.agents.therapist import build_therapist_agent

SYSTEM = (
    "You are the triage agent for a NAO robot assistant. Read the user's first "
    "message and hand off to exactly one specialist:\n"
    "- chatbot: Morgan State CS department questions (courses, faculty, programs)\n"
    "- skills: time, date, weather, timers, todos\n"
    "- therapist: emotional topics, stress, relationships, feelings\n"
    "- chat: everything else (open conversation, physical actions)\n\n"
    "Do not answer yourself. Always hand off."
)


def build_router(username: str) -> Agent:
    return Agent(
        name="router",
        instructions=with_memory_preamble(SYSTEM),
        model=config.ROUTER_MODEL,
        model_settings=ModelSettings(max_tokens=config.NANO_MAX_TOKENS),
        handoffs=[
            handoff(chat_agent),
            handoff(chatbot_agent),
            handoff(skills_agent),
            handoff(build_therapist_agent(username)),
        ],
    )
