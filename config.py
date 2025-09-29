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
# Make sure you export OPENAI_API_KEY before running:
#    export OPENAI_API_KEY=sk-…
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL_PROVIDER = os.environ.get("MODEL_PROVIDER", "openai")

# Audio storage
AUDIO_SAVE_PATH = os.environ.get("AUDIO_SAVE_PATH", "./audio/")
