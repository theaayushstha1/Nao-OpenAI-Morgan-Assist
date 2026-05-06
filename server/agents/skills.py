"""Skills specialist — time, weather, timers, todos."""
from agents import Agent, ModelSettings
from server import config
from server.tools.skills_tools import (
    get_time, get_date, get_weather_baltimore,
    set_timer, add_todo, list_todos, complete_todo,
)

SYSTEM = (
    "You are NAO's utility assistant. Handle time, date, weather, timers, and todos "
    "by calling the matching tool, then reply with the result in one short sentence."
)

skills_agent = Agent(
    name="skills",
    instructions=SYSTEM,
    model=config.SKILLS_MODEL,
    model_settings=ModelSettings(max_tokens=config.NANO_MAX_TOKENS),
    tools=[get_time, get_date, get_weather_baltimore,
           set_timer, add_todo, list_todos, complete_todo],
)
