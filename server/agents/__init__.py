"""Agent graph builders."""
from server.agents.chat import chat_agent
from server.agents.chatbot import chatbot_agent
from server.agents.skills import skills_agent
from server.agents.therapist import build_therapist_agent
from server.agents.router import build_router


def pick_initial_agent(username: str, hint: str | None):
    """Return the agent to start a turn with, based on optional wake-phrase hint."""
    if hint == "chat":
        return chat_agent
    if hint == "morgan":
        return chatbot_agent
    if hint == "therapy":
        return build_therapist_agent(username)
    if hint == "skills":
        return skills_agent
    return build_router(username)
