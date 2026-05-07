"""Time, weather, timers, reminders, todos. Replaces the old mini_nao."""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from agents import RunContextWrapper, function_tool

from server import session

_NY = ZoneInfo("America/New_York")
_BALT_LAT, _BALT_LON = 39.2904, -76.6122


def _unwrap(ctx) -> dict:
    return ctx.context if isinstance(ctx, RunContextWrapper) else ctx


# ────── time / date ──────

def _get_time_impl() -> dict:
    now = datetime.now(_NY)
    return {"time": now.strftime("%-I:%M %p"), "timezone": "America/New_York"}


@function_tool
def get_time() -> dict:
    """Current time in New York (Eastern time)."""
    return _get_time_impl()


@function_tool
def get_date() -> dict:
    """Today's date."""
    now = datetime.now(_NY)
    return {"date": now.strftime("%A, %B %-d, %Y")}


# ────── weather ──────

_WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow",
    80: "rain showers", 95: "thunderstorm",
}


def _get_weather_impl() -> dict:
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": _BALT_LAT, "longitude": _BALT_LON,
            "current": "temperature_2m,weather_code,relative_humidity_2m",
            "temperature_unit": "fahrenheit",
        },
        timeout=5,
    )
    data = r.json()["current"]
    return {
        "temperature_f": data["temperature_2m"],
        "condition": _WEATHER_CODES.get(data["weather_code"], "unknown"),
        "humidity": data["relative_humidity_2m"],
    }


@function_tool
def get_weather_baltimore() -> dict:
    """Current weather for Baltimore via Open-Meteo (no API key needed)."""
    return _get_weather_impl()


# ────── timers / reminders ──────

def _set_timer_impl(store: dict, seconds: int, label: str = "timer") -> dict:
    fire = int(time.time()) + max(1, seconds)
    tid = len(store.setdefault("timers", [])) + 1
    entry = {"id": tid, "fire_at": fire, "label": label}
    store["timers"].append(entry)
    return entry


@function_tool
def set_timer(ctx: RunContextWrapper, seconds: int, label: str = "timer") -> dict:
    """Set a timer that fires in `seconds` seconds. Label helps the user recognize it."""
    return _set_timer_impl(_unwrap(ctx), seconds, label)


# ────── todos ──────

def _add_todo_impl(store: dict, text: str) -> dict:
    items = store.setdefault("todos", [])
    tid = len(items) + 1
    entry = {"id": tid, "text": text, "done": False}
    items.append(entry)
    return entry


@function_tool
def add_todo(ctx: RunContextWrapper, text: str) -> dict:
    """Add a todo."""
    return _add_todo_impl(_unwrap(ctx), text)


def _list_todos_impl(store: dict) -> list[dict]:
    return [t for t in store.get("todos", []) if not t["done"]]


@function_tool
def list_todos(ctx: RunContextWrapper) -> list[dict]:
    """List open todos."""
    return _list_todos_impl(_unwrap(ctx))


def _complete_todo_impl(store: dict, todo_id: int) -> str:
    for t in store.get("todos", []):
        if t["id"] == todo_id:
            t["done"] = True
            return "done"
    return "not_found"


@function_tool
def complete_todo(ctx: RunContextWrapper, todo_id: int) -> str:
    """Mark a todo complete by id."""
    return _complete_todo_impl(_unwrap(ctx), todo_id)


# ────── camera consent ──────
# Tool path for "stop watching me" / "you can watch me again" when the regex
# in motion_trigger.py didn't fire but the LLM still understood the intent.
# Both paths converge on session.set_camera_consent(...) so the persisted
# state is identical regardless of which one wins the turn.
#
# Server-side handler note: the WebSocket path (app_ws.py — out of scope here)
# is responsible for emitting a `control { subtype: "camera_state",
# data: {enabled: <bool>} }` frame so the client UI flips immediately. The
# `camera-consent` agent owns the first-turn announce; these tools cover
# mid-conversation toggles.

def _disable_camera_impl(ctx) -> str:
    store = _unwrap(ctx)
    username = store.get("username", "guest")
    session.set_camera_consent(username, False)
    store["suppress_image"] = True
    return "camera disabled"


def _enable_camera_impl(ctx) -> str:
    store = _unwrap(ctx)
    username = store.get("username", "guest")
    session.set_camera_consent(username, True)
    store["suppress_image"] = False
    return "camera enabled"


@function_tool
def disable_camera(ctx: RunContextWrapper) -> str:
    """Turn off the camera for this user. Persists across sessions until
    re-enabled. Use when the user asks NAO to stop watching, looking, or
    recording (e.g. "stop watching me", "turn off the camera")."""
    return _disable_camera_impl(ctx)


@function_tool
def enable_camera(ctx: RunContextWrapper) -> str:
    """Turn the camera back on for this user. Use when the user explicitly
    invites NAO to watch again (e.g. "you can watch me again", "turn the
    camera on")."""
    return _enable_camera_impl(ctx)
