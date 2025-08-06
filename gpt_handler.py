# gpt_handler.py
# -*- coding: utf-8 -*-
"""
Handles sending chat history to OpenAI with function-calling enabled,
and returns either a text reply or a structured robot action.
"""

import os
import json
import openai

# Model configuration (you can override via environment)
MODEL = os.getenv("GPT_MODEL", "gpt-4-0613")

# ──────────────────────────────────────────────────────────────────────────────
# Define the set of functions (robot actions) the LLM can invoke.
# Each entry describes the action name, what it does, and its parameters.
# ──────────────────────────────────────────────────────────────────────────────
ROBOT_FUNCTIONS = [
    {
        "name": "stand_up",
        "description": "Have NAO stand up from sitting or crouching.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "sit_down",
        "description": "Have NAO sit down from standing position.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "kneel",
        "description": "Have NAO kneel on one knee.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "wave_hand",
        "description": "Wave one hand (left or right) at a given speed.",
        "parameters": {
            "type": "object",
            "properties": {
                "hand":  {"type": "string", "enum": ["left", "right"]},
                "speed": {"type": "number", "minimum": 0.1, "maximum": 1.0}
            },
            "required": ["hand"]
        }
    },
    {
        "name": "wave_both_hands",
        "description": "Wave both hands together.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "nod_head",
        "description": "Nod head a specified number of times.",
        "parameters": {
            "type": "object",
            "properties": {
                "times": {"type": "integer", "minimum": 1, "maximum": 5}
            },
            "required": ["times"]
        }
    },
    {
        "name": "shake_head",
        "description": "Shake head (like saying 'no') a specified number of times.",
        "parameters": {
            "type": "object",
            "properties": {
                "times": {"type": "integer", "minimum": 1, "maximum": 5}
            },
            "required": ["times"]
        }
    },
    {
        "name": "clap_hands",
        "description": "Clap hands a specified number of times.",
        "parameters": {
            "type": "object",
            "properties": {
                "times": {"type": "integer", "minimum": 1, "maximum": 5}
            },
            "required": ["times"]
        }
    },
    {
        "name": "dance",
        "description": "Play a short dance animation (options: robot, hiphop, salsa).",
        "parameters": {
            "type": "object",
            "properties": {
                "style": {"type": "string", "enum": ["robot", "hiphop", "salsa"]}
            },
            "required": ["style"]
        }
    },
    {
        "name": "spin",
        "description": "Spin in place by a given angle in degrees.",
        "parameters": {
            "type": "object",
            "properties": {
                "degrees": {"type": "number", "minimum": 0.0}
            },
            "required": ["degrees"]
        }
    },
    {
        "name": "change_eye_color",
        "description": "Set NAO’s eye LEDs to a given color.",
        "parameters": {
            "type": "object",
            "properties": {
                "color": {
                    "type": "string",
                    "enum": ["red", "green", "blue", "yellow", "purple", "white"]
                }
            },
            "required": ["color"]
        }
    },
    {
        "name": "move_forward",
        "description": "Move forward by a specified distance (meters).",
        "parameters": {
            "type": "object",
            "properties": {
                "meters": {"type": "number", "minimum": 0.0}
            },
            "required": ["meters"]
        }
    },
    {
        "name": "move_backward",
        "description": "Move backward by a specified distance (meters).",
        "parameters": {
            "type": "object",
            "properties": {
                "meters": {"type": "number", "minimum": 0.0}
            },
            "required": ["meters"]
        }
    },
    {
        "name": "turn_left",
        "description": "Rotate left by a specified angle (degrees).",
        "parameters": {
            "type": "object",
            "properties": {
                "degrees": {"type": "number", "minimum": 0.0}
            },
            "required": ["degrees"]
        }
    },
    {
        "name": "turn_right",
        "description": "Rotate right by a specified angle (degrees).",
        "parameters": {
            "type": "object",
            "properties": {
                "degrees": {"type": "number", "minimum": 0.0}
            },
            "required": ["degrees"]
        }
    },
    {
        "name": "follow_movement",
        "description": "Enter a mode where NAO mirrors the user’s upper-body motions.",
        "parameters": {"type": "object", "properties": {}}
    }
]

def get_reply(messages):
    """
    Send conversation history to OpenAI and handle optional function calls.

    Args:
      messages (list of dict): Chat messages, each with 'role' and 'content'.

    Returns:
      dict:
        - 'reply': Text response (empty if a function is called).
        - 'function_call': {'name': str, 'args': dict} or None.
    """
    print("Calling OpenAI chat.completions.create with function-calling...")
    try:
        resp = openai.chat.completions.create(
            model=MODEL,
            messages=messages,
            functions=ROBOT_FUNCTIONS,
            function_call="auto"
        )
        choice = resp.choices[0].message

        # Check for function call
        func_call = None
        if getattr(choice, "function_call", None):
            fname = choice.function_call.name
            fargs = json.loads(choice.function_call.arguments)
            func_call = {"name": fname, "args": fargs}

        return {
            "reply": (choice.content or "").strip(),
            "function_call": func_call
        }

    except Exception as e:
        print("⚠️ OpenAI API error:", e)
        return {
            "reply": "Sorry, I couldn't process that right now.",
            "function_call": None
        }
