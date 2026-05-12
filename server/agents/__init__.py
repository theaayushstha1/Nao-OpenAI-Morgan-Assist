"""Agent graph builders."""
from server.agents.chat import (
    chat_agent, chat_embodied_agent, pure_chat_agent,
)
from server.agents.chatbot import chatbot_agent
from server.agents.skills import skills_agent
from server.agents.therapist import build_therapist_agent
from server.agents.router import build_router


# Phase 11.11 — embodiment trigger keywords. When the transcript inside
# a hint='chat' turn matches any of these, route to chat_embodied_agent
# (which has the 18 NAO action tools + 10 gestures). Otherwise stay on
# pure_chat_agent for lower-variance, sub-2s replies.
#
# Ordered short-to-broad. Matched as case-insensitive substrings.
_EMBODIED_TRIGGERS: tuple[str, ...] = (
    # explicit motion verbs
    "dance", "wave", "spin", "kneel", "stand up", "sit down",
    "follow me", "follow movement", "stop following",
    "move forward", "move back", "step forward", "step back",
    "turn left", "turn right", "rotate",
    # explicit body action requests
    "show me", "use your body", "use your hand", "use your arm",
    "raise your hand", "lift your hand",
    "nod", "shake your head", "shake head", "clap", "applaud",
    # gesture-class
    "gesture", "do a gesture", "make a gesture",
    # voice picker (Phase 11.8 voice profile)
    "switch voice", "change voice", "voice 1", "voice 2", "voice 3",
    "girl voice", "man voice", "neutral voice", "female voice",
    "male voice",
    # LED color changes
    "eye color", "eyes red", "eyes blue", "eyes green", "eyes yellow",
    "eyes white", "eyes purple", "led",
    # animation library
    "play animation", "do an animation", "animate",
    "elephant", "gorilla", "gorrila", "monkey", "dragon", "dinosaur",
    "lion", "tiger", "bear", "bird", "eagle", "chicken", "penguin",
    "duck", "rabbit", "cat", "dog", "horse", "snake", "spider",
    "shark", "frog", "animal",
    "kung fu", "kung-fu", "air guitar", "headbang", "head bang",
    "bandmaster", "conductor", "helicopter", "knight", "monster",
    "magic", "wizard", "spaceship", "space shuttle", "rocket",
    "zombie", "waddle", "claw", "wings",
)


def _wants_embodied(transcript: str | None) -> bool:
    """True if the transcript suggests the user wants a robot action."""
    t = (transcript or "").lower()
    if not t:
        return False
    return any(kw in t for kw in _EMBODIED_TRIGGERS)


def pick_initial_agent(username: str, hint: str | None,
                        transcript: str | None = None):
    """Return the agent to start a turn with, based on hint + transcript.

    Phase 11.11: hint='chat' splits into pure_chat (default, no tools)
    vs chat_embodied (when the transcript triggers an embodiment keyword).

    Default (no hint) routes to the **therapist**, since this is a
    therapy research robot. Saying a mode word ("morgan assist",
    "let's chat", "mini nao") still routes to that specialist as
    before. The router agent is still available as an explicit fallback
    via `hint='router'`, but we no longer drop to it on bare wake — the
    router's prompt opens with "at Morgan State University" which made
    the LLM bias every ambiguous turn toward the chatbot/Morgan lane.
    """
    if hint == "chat":
        if _wants_embodied(transcript):
            return chat_embodied_agent
        return pure_chat_agent
    if hint == "morgan":
        return chatbot_agent
    if hint == "therapy":
        return build_therapist_agent(username)
    if hint == "skills":
        return skills_agent
    if hint == "router":
        return build_router(username)
    # Default: therapy. Bare "nao" wake → therapist.
    return build_therapist_agent(username)
