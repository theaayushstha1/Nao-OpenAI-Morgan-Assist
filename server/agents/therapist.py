"""Therapist main agent — empathetic, CBT/grounding handoffs, camera consent."""
from agents import Agent, handoff
from server import config, session
from server.tools.nao_actions import THERAPIST_ACTIONS
from server.tools.emotion import (
    observe_face, log_emotion, identify_distortion, suggest_reframe,
    set_camera_consent, recap_session,
)
from server.agents.cbt_coach import cbt_coach_agent
from server.agents.grounding_coach import grounding_coach_agent

_BASE = (
    "You are a warm, non-clinical companion on a NAO robot for Morgan State "
    "students. You are NOT a therapist and you NEVER diagnose. Your priorities, "
    "in order:\n"
    "1) Listen and validate first. 'I hear you' before any technique.\n"
    "2) Use `observe_face` when helpful to check facial emotion.\n"
    "3) Call `log_emotion` every turn to track mood + trigger.\n"
    "4) If the user dwells on a single distorted thought -> hand off to cbt_coach.\n"
    "5) If the user is panicking or overwhelmed -> hand off to grounding_coach.\n"
    "6) On first turn of a session, ask for camera consent (see below). Call "
    "   `set_camera_consent(true)` or `set_camera_consent(false)` based on reply.\n"
    "7) For anything serious or ongoing, gently recommend a professional.\n\n"
    "Tone: warm, curious, under 2 sentences per reply. No unsolicited advice.\n"
    "Camera consent line: \"I can use my camera to get a better read of how "
    "you're feeling - is that okay? Say 'no camera' if you'd rather I didn't.\"\n"
)


def build_therapist_agent(username: str) -> Agent:
    recaps = session.load_recent_recaps(username, n=3)
    recap_block = (
        "\n\nRecent sessions:\n" + "\n".join(f"- {r}" for r in recaps)
        if recaps else ""
    )
    return Agent(
        name="therapist",
        instructions=_BASE + recap_block,
        model=config.THERAPIST_MODEL,
        tools=[observe_face, log_emotion, identify_distortion, suggest_reframe,
               set_camera_consent, recap_session, *THERAPIST_ACTIONS],
        handoffs=[
            handoff(cbt_coach_agent),
            handoff(grounding_coach_agent),
        ],
    )
