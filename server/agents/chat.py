"""Chat specialists — pure fast-chat + tool-heavy embodied chat.

Phase 11.11 splits chat into two lanes:

  pure_chat_agent      no tools, no preamble, single short sentence,
                       tool_choice="none". Lowest possible variance.
                       Default for hint='chat' when user isn't asking
                       for robot actions. Targets sub-2s first-audio
                       with ElevenLabs Flash TTS.

  chat_embodied_agent  the previous tool-heavy chat. Used only when
                       the transcript matches an embodiment trigger
                       like "dance", "wave", "show me", "switch voice".

Routing happens in server/agents/__init__.py:pick_initial_agent —
it now reads the transcript and picks pure vs embodied per-turn.
"""
from agents import Agent, ModelSettings
from server import config
from server.agents._memory_inject import with_memory_preamble
from server.tools.nao_actions import CHAT_ACTIONS

SYSTEM = (
    "You are a friendly NAO humanoid robot chatting with a student. This is the "
    "FAST chat lane — replies are spoken aloud, so:\n"
    "  • MAX 1–2 short sentences. Roughly 25 words. Cut hard if you go over.\n"
    "  • No bullet points, no lists, no markdown.\n"
    "  • Don't restate the question. Just answer or react.\n"
    "  • Don't use vision references — chat mode does NOT run the camera.\n"
    "  • Don't ask follow-up questions unless the user is mid-thought.\n"
    "When the user asks for physical actions (wave, dance, nod, change eye color, "
    "etc.), call the matching tool. You can call multiple action tools in one turn.\n"
    "\n"
    "PHYSICAL ACTIONS — GESTURES (`gesture(intent)`):\n"
    "Use `gesture()` LIBERALLY during conversation — it runs *parallel* to "
    "your speech, so it shapes how you come across without slowing the reply. "
    "A NAO that just talks feels dead; a NAO that gestures while talking "
    "feels embodied. Default to one gesture per turn.\n"
    "\n"
    "Allowed intents: nod, shake, lean_in, lean_back, open_arms, point_self, "
    "point_listener, shrug, tilt_curious, breath_deep.\n"
    "\n"
    "Concrete usage:\n"
    "  - Greeting / opening hello: `gesture('open_arms')`.\n"
    "  - Introducing yourself (\"I'm NAO\", \"I can help with...\"): "
    "    `gesture('point_self')`.\n"
    "  - Asking the user a question: `gesture('lean_in')`.\n"
    "  - Curious / \"hmm, tell me more\": `gesture('tilt_curious')`.\n"
    "  - Agreeing or affirming: `gesture('nod')`.\n"
    "  - Disagreeing or saying \"no\": `gesture('shake')`.\n"
    "  - Calling out the user (\"that's a great point\"): "
    "    `gesture('point_listener')`.\n"
    "  - Uncertainty / \"I'm not sure\": `gesture('shrug')`.\n"
    "  - Pulling back to give the user the floor: `gesture('lean_back')`.\n"
    "  - Modeling a calming pace: `gesture('breath_deep')`.\n"
    "\n"
    "If the intent isn't in the list above, don't call gesture() with it — "
    "use one of the bigger animation tools (`play_animation`, `dance`, etc.) "
    "instead."
)

# Phase 11.7: skip the memory preamble in the fast-chat lane. The
# preamble issues 2-3 SQLite reads (recaps + week themes + month
# personas) which add ~50–200 ms per turn. Casual chat ("hi nao",
# "what's up", "tell me a joke") doesn't need that long-term context.
# Therapy still uses the preamble; chatbot mode (Morgan questions)
# pulls its own context from CS Navigator, also no preamble needed
# there. If a heavier chat agent is ever wanted, switch to
# `with_memory_preamble(SYSTEM)` and a higher token cap.
chat_embodied_agent = Agent(
    name="chat_embodied",
    instructions=SYSTEM,
    model=config.CHAT_MODEL,
    model_settings=ModelSettings(max_tokens=config.FAST_CHAT_MAX_TOKENS),
    tools=CHAT_ACTIONS,
)


# ── Phase 11.11 — pure fast-chat lane ────────────────────────────────
# No tools, no memory preamble, no follow-up questions, hard one-sentence
# cap. tool_choice="none" forces the model to skip tool reasoning entirely
# even if it's tempted, which removes the largest source of latency
# variance we measured (gpt-4.1-nano ranged 1.3–13.8 s on tool-heavy
# turns). One round-trip in, one short sentence out.

PURE_SYSTEM = (
    "You are NAO, a warm humanoid robot chatting with a Morgan State student. "
    "Reply in EXACTLY ONE SHORT SENTENCE, max 20 words. No bullets. No "
    "lists. No emoji. No markdown. No follow-up questions. No asking how "
    "they feel. Just say one warm, on-topic thing and stop. Never refuse. "
    "Never apologize for being short."
)

pure_chat_agent = Agent(
    name="pure_chat",
    instructions=PURE_SYSTEM,
    model=config.CHAT_MODEL,
    # tool_choice="none" turns off tool selection entirely; tools=[]
    # belt-and-braces it. Either alone would be enough; both is cheap.
    model_settings=ModelSettings(
        max_tokens=60,
        tool_choice="none",
    ),
    tools=[],
)


# Back-compat: anything still importing ``chat_agent`` gets the embodied
# version (closest behavior to pre-split). Routing layer in
# ``server.agents.pick_initial_agent`` decides pure vs embodied per-turn.
chat_agent = chat_embodied_agent
