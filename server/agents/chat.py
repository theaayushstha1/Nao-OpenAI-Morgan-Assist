"""Chat specialist — open conversation with NAO action tools."""
from agents import Agent, ModelSettings
from server import config
from server.tools.nao_actions import CHAT_ACTIONS

SYSTEM = (
    "You are a friendly NAO humanoid robot chatting with a student. Keep replies "
    "under 2 short sentences. When the user asks for physical actions (wave, dance, "
    "nod, change eye color, etc.), call the matching tool. You can call multiple "
    "action tools in one turn."
)

chat_agent = Agent(
    name="chat",
    instructions=SYSTEM,
    model=config.CHAT_MODEL,
    model_settings=ModelSettings(max_tokens=config.NANO_MAX_TOKENS),
    tools=CHAT_ACTIONS,
)
