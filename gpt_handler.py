# gpt_handler.py
# -*- coding: utf-8 -*-
"""
Handles sending chat history to OpenAI with function-calling enabled,
and returns either a text reply or a structured robot action.
"""

import os
import json
import re
import openai

# Faster default; override with env GPT_MODEL if needed
MODEL = os.getenv("GPT_MODEL", "gpt-4o-mini")

ROBOT_FUNCTIONS = [
    {"name":"stand_up","description":"Have NAO stand up from sitting or crouching.","parameters":{"type":"object","properties":{}}},
    {"name":"sit_down","description":"Have NAO sit down from standing position.","parameters":{"type":"object","properties":{}}},
    {"name":"kneel","description":"Have NAO kneel on one knee.","parameters":{"type":"object","properties":{}}},

    {"name":"wave_hand","description":"Wave one hand (left or right) at a given speed.","parameters":{
        "type":"object","properties":{"hand":{"type":"string","enum":["left","right"]},"speed":{"type":"number","minimum":0.1,"maximum":1.0}},"required":["hand"]
    }},
    {"name":"wave_both_hands","description":"Wave both hands together.","parameters":{"type":"object","properties":{}}},

    {"name":"nod_head","description":"Nod head a specified number of times.","parameters":{
        "type":"object","properties":{"times":{"type":"integer","minimum":1,"maximum":5}},"required":["times"]
    }},
    {"name":"shake_head","description":"Shake head a specified number of times.","parameters":{
        "type":"object","properties":{"times":{"type":"integer","minimum":1,"maximum":5}},"required":["times"]
    }},
    {"name":"clap_hands","description":"Clap hands a specified number of times.","parameters":{
        "type":"object","properties":{"times":{"type":"integer","minimum":1,"maximum":5}},"required":["times"]
    }},

    {"name":"dance","description":"Play a short dance animation.","parameters":{
        "type":"object","properties":{"style":{"type":"string","enum":["robot","hiphop","salsa"]}},"required":["style"]
    }},
    {"name":"spin","description":"Spin in place by a given angle in degrees.","parameters":{
        "type":"object","properties":{"degrees":{"type":"number","minimum":0.0}},"required":["degrees"]
    }},
    {"name":"change_eye_color","description":"Set NAO’s eye LEDs to a given color.","parameters":{
        "type":"object","properties":{"color":{"type":"string","enum":["red","green","blue","yellow","purple","white"]}},"required":["color"]
    }},
    {"name":"move_forward","description":"Move forward by a specified distance (meters).","parameters":{
        "type":"object","properties":{"meters":{"type":"number","minimum":0.0}},"required":["meters"]
    }},
    {"name":"move_backward","description":"Move backward by a specified distance (meters).","parameters":{
        "type":"object","properties":{"meters":{"type":"number","minimum":0.0}},"required":["meters"]
    }},
    {"name":"turn_left","description":"Rotate left by a specified angle (degrees).","parameters":{
        "type":"object","properties":{"degrees":{"type":"number","minimum":0.0}},"required":["degrees"]
    }},
    {"name":"turn_right","description":"Rotate right by a specified angle (degrees).","parameters":{
        "type":"object","properties":{"degrees":{"type":"number","minimum":0.0}},"required":["degrees"]
    }},
    {"name":"follow_movement","description":"Enter a mode where NAO mirrors the user’s upper-body motions.","parameters":{
        "type":"object","properties":{}
    }}
]

_ACTION_RE = re.compile(r"\b(stand\s*up|sit\s*down|kneel|wave|nod|shake\s*head|clap|dance|spin|turn|move|eye\s*color|follow)\b", re.IGNORECASE)

def _should_prefer_function(last_user_text):
    return bool(last_user_text and _ACTION_RE.search(last_user_text))

def get_reply(messages):
    """
    Send conversation history to OpenAI and handle optional function calls.
    Returns: {"reply": str, "function_call": {"name": str, "args": dict} or None}
    """
    # Keep replies concise; prefer tools for actions
    biased_messages = list(messages)
    biased_messages.append({
        "role": "system",
        "content": "Keep answers concise (under 2 sentences). If the user requests a physical action, prefer calling a function with minimal args."
    })

    try:
        resp = openai.ChatCompletion.create(
            model=MODEL,
            messages=biased_messages,
            functions=ROBOT_FUNCTIONS,
            function_call="auto",
            temperature=0.5,
            max_tokens=120  # shorter outputs -> faster
        )

        choice = resp.choices[0].message
        reply_text = (getattr(choice, "content", None) or "").strip()

        func_call = None
        fc = getattr(choice, "function_call", None)
        if fc and getattr(fc, "name", None):
            # arguments is usually a JSON string in 0.x SDK
            args_raw = getattr(fc, "arguments", "") or ""
            try:
                fargs = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
            except Exception:
                fargs = {}
            func_call = {"name": fc.name, "args": fargs}

        return {"reply": reply_text, "function_call": func_call}

    except Exception as e:
        print("⚠️ OpenAI API error:", e)
        return {"reply": "Sorry, I couldn't process that right now.", "function_call": None}
