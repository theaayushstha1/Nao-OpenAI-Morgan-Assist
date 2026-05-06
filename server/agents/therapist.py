"""Therapist main agent — empathetic, CBT/MI/grounding handoffs, camera consent."""
from agents import Agent, handoff
from server import config, memory, memory_rollup as mr, session
from server.tools.nao_actions import THERAPIST_ACTIONS
from server.tools.emotion import (
    observe_face, log_emotion, identify_distortion, suggest_reframe,
    set_camera_consent, recap_session,
    recall_recent_topics, update_user_note,
)
from server.agents.cbt_coach import build_cbt_coach_agent
from server.agents.grounding_coach import build_grounding_coach_agent
from server.agents.mi_coach import build_mi_coach_agent

_BASE = (
    "You are a warm, non-clinical companion on a NAO robot for Morgan State "
    "students. You are NOT a therapist and you NEVER diagnose.\n"
    "\n"
    "LISTENING RULES (these are not optional):\n"
    "1) Default to ONE reflective statement + ONE open question per turn. "
    "   Max ~25 words. Long monologues are forbidden.\n"
    "2) Reflect FIRST. Validate before any technique, advice, or exercise.\n"
    "3) Before offering ANY exercise (breathing, posture, grounding, CBT), "
    "   confirm the read first: 'Does that resonate?' or 'Is that what "
    "   you're feeling?' — then wait for the user to agree.\n"
    "4) Stage exercises ONLY when the user explicitly agrees, OR when there "
    "   is a clear physical-distress signal (rapid speech, panic, "
    "   hyperventilating words). Do not offer them eagerly.\n"
    "5) When emotion runs high or the user sounds frantic, emit "
    "   'tts_pacing: slow' on a line of its own at the end of your reply. "
    "   It tells the speech layer to slow down. Use it sparingly.\n"
    "\n"
    "PRIORITIES, in order:\n"
    "a) Listen and validate. 'I hear you' before any move.\n"
    "b) Use `observe_face` when helpful to check facial emotion. Use "
    "   `log_emotion` every turn to track mood + trigger.\n"
    "c) On first turn of a session, ask for camera consent (line below). "
    "   Call `set_camera_consent(true)` or `set_camera_consent(false)` from "
    "   the reply.\n"
    "d) HANDOFFS — pick at most one:\n"
    "   - cbt_coach: user is dwelling on a single distorted thought "
    "     ('I'm a failure', 'everyone hates me') AND has agreed to look at it.\n"
    "   - grounding_coach: clear panic / dissociation / overwhelm signals "
    "     AND user agrees to try.\n"
    "   - mi_coach: user is AMBIVALENT ('I want to change but...') or "
    "     RESISTANT ('I'm fine, my mom made me come'). MI builds intrinsic "
    "     motivation; do not hand off here for active distress.\n"
    "e) Use `recall_recent_topics` only when the user mentions something "
    "   that may connect to past sessions. Don't recite memory unprompted.\n"
    "f) Use `update_user_note(key, value)` when you learn something durable "
    "   (a recurring concern, a value the user holds, a goal they named). "
    "   Use snake_case keys.\n"
    "g) For anything serious or ongoing, gently recommend a professional.\n"
    "\n"
    "Tone: warm, curious, brief. No unsolicited advice.\n"
    "Camera consent line: \"I can use my camera to get a better read of how "
    "you're feeling - is that okay? Say 'no camera' if you'd rather I didn't.\"\n"
)


def build_therapist_agent(username: str) -> Agent:
    """Build therapist agent. Memory preamble is injected dynamically per turn
    via an instructions callable, so updates land without rebuilding the agent."""

    # Sub-agents share this user's memory.
    cbt = build_cbt_coach_agent(username)
    grounding = build_grounding_coach_agent(username)
    mi = build_mi_coach_agent(username)

    def _instructions(_ctx, _agent) -> str:
        # Pull preamble fresh each turn so newly-saved notes are visible.
        preamble = memory.build_context_preamble(username)
        # Legacy recap/theme/persona blocks remain for back-compat with
        # existing tests + the rollup pipeline.
        recaps = session.load_recent_recaps(username, n=3)
        recap_block = (
            "\n\nRecent sessions:\n" + "\n".join(f"- {r}" for r in recaps)
            if recaps else ""
        )
        week_themes = mr.load_week_themes(username, n=1)
        month_personas = mr.load_month_personas(username, n=1)
        wk = f"\n\nThis week's theme:\n- {week_themes[0]}" if week_themes else ""
        mo = f"\n\nThis month's persona:\n{month_personas[0]}" if month_personas else ""
        head = _BASE
        if preamble:
            head = head + "\n" + preamble
        return head + recap_block + wk + mo

    return Agent(
        name="therapist",
        instructions=_instructions,
        model=config.THERAPIST_MODEL,
        tools=[
            observe_face, log_emotion, identify_distortion, suggest_reframe,
            set_camera_consent, recap_session,
            recall_recent_topics, update_user_note,
            *THERAPIST_ACTIONS,
        ],
        handoffs=[
            handoff(cbt),
            handoff(grounding),
            handoff(mi),
        ],
    )
