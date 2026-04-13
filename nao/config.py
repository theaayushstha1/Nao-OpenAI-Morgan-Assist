# -*- coding: utf-8 -*-
"""
Configuration for NAO ⇄ OpenAI integration.
Reads everything from environment variables.
"""

import os

# NAO connection settings
NAO_IP   = os.environ.get("NAO_IP", "172.20.95.111")
NAO_PORT = int(os.environ.get("NAO_PORT", "9559"))

# OpenAI settings

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL_PROVIDER = os.environ.get("MODEL_PROVIDER", "openai")

# Server IP (for NAO-side scripts to reach the Flask server)
SERVER_IP = os.environ.get("SERVER_IP", "172.20.95.105")

# Audio storage
AUDIO_SAVE_PATH = os.environ.get("AUDIO_SAVE_PATH", "./audio/")
