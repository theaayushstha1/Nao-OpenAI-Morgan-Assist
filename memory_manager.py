# memory_manager.py
# -*- coding: utf-8 -*-
"""
Manage per-user chat histories:
  - Load existing history or start fresh when a user is initialized
  - Append user/bot turns in memory
  - Retrieve history for GPT context
  - Persist history on disk in JSON format
"""

import os
import json

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
BASE_DIR      = "memory"  # directory to store per-user history files
SYSTEM_PROMPT = "You are a helpful robot assistant."
# ──────────────────────────────────────────────────────────────────────────────

# In-memory store of histories, keyed by username
_histories = {}
_current_user = None

def _ensure_base_dir():
    """Create the base memory directory if it doesn't exist."""
    if not os.path.exists(BASE_DIR):
        os.makedirs(BASE_DIR)

def _user_filepath(user):
    """Return a safe file path for the given user."""
    safe_name = user.strip().replace(" ", "_")
    return os.path.join(BASE_DIR, "{}.json".format(safe_name))

def initialize_user(user):
    """
    Load a user's history from disk (if any) or start a new one.
    Must be called once before adding messages for that user.
    """
    global _current_user
    _ensure_base_dir()
    _current_user = user
    path = _user_filepath(user)

    if os.path.exists(path):
        with open(path, "r") as f:
            _histories[user] = json.load(f)
    else:
        # Start fresh with a system prompt
        _histories[user] = [{"role": "system", "content": SYSTEM_PROMPT}]

def add_user_message(text):
    """
    Append a user message to the current user's history.
    """
    if _current_user is None:
        raise RuntimeError("No user initialized. Call initialize_user(user) first.")
    _histories[_current_user].append({
        "role": "user",
        "content": text
    })

def add_bot_reply(reply):
    """
    Append a bot reply to the current user's history.
    """
    if _current_user is None:
        raise RuntimeError("No user initialized. Call initialize_user(user) first.")
    _histories[_current_user].append({
        "role": "assistant",
        "content": reply
    })

def get_chat_history():
    """
    Get the full chat history (list of messages) for the current user.
    """
    if _current_user is None:
        raise RuntimeError("No user initialized. Call initialize_user(user) first.")
    return _histories[_current_user]

def reset_memory():
    """
    Reset the current user's in-memory history back to just the system prompt.
    """
    if _current_user is None:
        raise RuntimeError("No user initialized. Call initialize_user(user) first.")
    _histories[_current_user] = [{"role": "system", "content": SYSTEM_PROMPT}]

def save_chat_history():
    """
    Persist the current user's history to disk as JSON.
    """
    if _current_user is None:
        raise RuntimeError("No user initialized. Call initialize_user(user) first.")
    path = _user_filepath(_current_user)
    with open(path, "w") as f:
        json.dump(_histories[_current_user], f, indent=2)
