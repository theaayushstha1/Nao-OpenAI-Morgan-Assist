# gpt_handler.py
# -*- coding: utf-8 -*-
"""
Handles sending chat history to OpenAI with tool-calling enabled,
and returns either a text reply or a structured robot action.
"""

import os
import json
import re
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Faster default; override with env GPT_MODEL if needed
MODEL = os.getenv("GPT_MODEL", "gpt-4o-mini")

ROBOT_TOOLS = [
    {"type": "function", "function": {"name":"stand_up","description":"Have NAO stand up from sitting or crouching.","parameters":{"type":"object","properties":{}}}},
    {"type": "function", "function": {"name":"sit_down","description":"Have NAO sit down from standing position.","parameters":{"type":"object","properties":{}}}},
    {"type": "function", "function": {"name":"kneel","description":"Have NAO kneel on one knee.","parameters":{"type":"object","properties":{}}}},

    {"type": "function", "function": {"name":"wave_hand","description":"Wave one hand (left or right) at a given speed.","parameters":{
        "type":"object","properties":{"hand":{"type":"string","enum":["left","right"]},"speed":{"type":"number","minimum":0.1,"maximum":1.0}},"required":["hand"]
    }}},
    {"type": "function", "function": {"name":"wave_both_hands","description":"Wave both hands together.","parameters":{"type":"object","properties":{}}}},

    {"type": "function", "function": {"name":"nod_head","description":"Nod head a specified number of times.","parameters":{
        "type":"object","properties":{"times":{"type":"integer","minimum":1,"maximum":5}},"required":["times"]
    }}},
    {"type": "function", "function": {"name":"shake_head","description":"Shake head a specified number of times.","parameters":{
        "type":"object","properties":{"times":{"type":"integer","minimum":1,"maximum":5}},"required":["times"]
    }}},
    {"type": "function", "function": {"name":"clap_hands","description":"Clap hands a specified number of times.","parameters":{
        "type":"object","properties":{"times":{"type":"integer","minimum":1,"maximum":5}},"required":["times"]
    }}},

    {"type": "function", "function": {"name":"dance","description":"Play a short dance animation.","parameters":{
        "type":"object","properties":{"style":{"type":"string","enum":["robot","hiphop","salsa"]}},"required":["style"]
    }}},
    {"type": "function", "function": {"name":"spin","description":"Spin in place by a given angle in degrees.","parameters":{
        "type":"object","properties":{"degrees":{"type":"number","minimum":0.0}},"required":["degrees"]
    }}},
    {"type": "function", "function": {"name":"change_eye_color","description":"Set NAO's eye LEDs to a given color.","parameters":{
        "type":"object","properties":{"color":{"type":"string","enum":["red","green","blue","yellow","purple","white"]}},"required":["color"]
    }}},
    {"type": "function", "function": {"name":"move_forward","description":"Move forward by a specified distance (meters).","parameters":{
        "type":"object","properties":{"meters":{"type":"number","minimum":0.0}},"required":["meters"]
    }}},
    {"type": "function", "function": {"name":"move_backward","description":"Move backward by a specified distance (meters).","parameters":{
        "type":"object","properties":{"meters":{"type":"number","minimum":0.0}},"required":["meters"]
    }}},
    {"type": "function", "function": {"name":"turn_left","description":"Rotate left by a specified angle (degrees).","parameters":{
        "type":"object","properties":{"degrees":{"type":"number","minimum":0.0}},"required":["degrees"]
    }}},
    {"type": "function", "function": {"name":"turn_right","description":"Rotate right by a specified angle (degrees).","parameters":{
        "type":"object","properties":{"degrees":{"type":"number","minimum":0.0}},"required":["degrees"]
    }}},
    {"type": "function", "function": {"name":"follow_movement","description":"Enter a mode where NAO mirrors the user's upper-body motions.","parameters":{
        "type":"object","properties":{}
    }}}
]

_ACTION_RE = re.compile(r"\b(stand\s*up|sit\s*down|kneel|wave|nod|shake\s*head|clap|dance|spin|turn|move|eye\s*color|follow)\b", re.IGNORECASE)

def _should_prefer_function(last_user_text):
    return bool(last_user_text and _ACTION_RE.search(last_user_text))

def get_reply(messages):
    """
    Send conversation history to OpenAI and handle optional tool calls.
    Returns: {"reply": str, "function_call": {"name": str, "args": dict} or None}
    """
    # Keep replies concise; prefer tools for actions
    biased_messages = list(messages)
    biased_messages.append({
        "role": "system",
        "content": "Keep answers concise (under 2 sentences). If the user requests a physical action, prefer calling a function with minimal args."
    })

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=biased_messages,
            tools=ROBOT_TOOLS,
            tool_choice="auto",
            temperature=0.5,
            max_tokens=120
        )

        choice = resp.choices[0].message
        reply_text = (choice.content or "").strip()

        func_call = None
        if choice.tool_calls:
            tc = choice.tool_calls[0]
            args_raw = tc.function.arguments or ""
            try:
                fargs = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
            except Exception:
                fargs = {}
            func_call = {"name": tc.function.name, "args": fargs}

        return {"reply": reply_text, "function_call": func_call}

    except Exception as e:
        print("OpenAI API error:", e)
        return {"reply": "Sorry, I couldn't process that right now.", "function_call": None}
