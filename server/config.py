"""Environment-driven configuration for the server."""
import os
from dotenv import load_dotenv

load_dotenv()

# OpenAI
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "gpt-4.1-nano")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "gpt-4.1-nano")
CHATBOT_MODEL = os.environ.get("CHATBOT_MODEL", "gpt-4.1-mini")
THERAPIST_MODEL = os.environ.get("THERAPIST_MODEL", "gpt-4.1-mini")
SKILLS_MODEL = os.environ.get("SKILLS_MODEL", "gpt-4.1-nano")
CRISIS_MODEL = os.environ.get("CRISIS_MODEL", "gpt-4.1")
CBT_MODEL = os.environ.get("CBT_MODEL", "gpt-4.1-mini")
GROUNDING_MODEL = os.environ.get("GROUNDING_MODEL", "gpt-4.1-mini")

# Per-agent max output tokens. Nano agents are capped tightly to keep replies
# snappy (under 2 short sentences). Mini agents get a bit more headroom for
# clinical reasoning / RAG synthesis but still stay terse.
NANO_MAX_TOKENS = int(os.environ.get("NANO_MAX_TOKENS", "200"))
MINI_MAX_TOKENS = int(os.environ.get("MINI_MAX_TOKENS", "400"))
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "gpt-4o-mini-transcribe")

# Deepgram Nova-2 streaming/prerecorded ASR. When USE_DEEPGRAM is true and the
# API key is present, /turn and /stream_turn use Deepgram instead of Whisper.
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
DEEPGRAM_MODEL = os.environ.get("DEEPGRAM_MODEL", "nova-2")
DEEPGRAM_LANGUAGE = os.environ.get("DEEPGRAM_LANGUAGE", "en-US")
USE_DEEPGRAM = bool(DEEPGRAM_API_KEY) and os.environ.get("USE_DEEPGRAM", "1") == "1"
REALTIME_MODEL = os.environ.get("REALTIME_MODEL", "gpt-realtime")
REALTIME_VAD_THRESHOLD = float(os.environ.get("REALTIME_VAD_THRESHOLD", "0.30"))
REALTIME_VAD_PREFIX_MS = int(os.environ.get("REALTIME_VAD_PREFIX_MS", "500"))
REALTIME_VAD_SILENCE_MS = int(os.environ.get("REALTIME_VAD_SILENCE_MS", "450"))

# ElevenLabs voice clone (optional). When ELEVENLABS_API_KEY + VOICE_ID are
# set, /stream_turn synthesizes each sentence with ElevenLabs and emits an
# "audio" SSE event (base64 mp3) alongside the "sentence" event so NAO can
# play the cloned voice via ALAudioPlayer instead of its onboard TTS.
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "")
ELEVENLABS_MODEL_ID = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5")
USE_ELEVENLABS = bool(ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID)

# Vertex AI Search (Morgan State CS knowledge base)
GOOGLE_CLOUD_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "csnavigator-vertex-ai")
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "us")
VERTEX_DATASTORE_ID = os.environ.get("VERTEX_DATASTORE_ID", "csnavigator-kb-v7")

# Networking
NAO_IP = os.environ.get("NAO_IP", "172.20.95.111")
NAO_PORT = int(os.environ.get("NAO_PORT", "9559"))
SERVER_IP = os.environ.get("SERVER_IP", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "5000"))

# Off by default because /greet speaks proactively when a person is detected.
PROACTIVE_GREET_ENABLED = os.environ.get("PROACTIVE_GREET_ENABLED", "0") == "1"

# Persistence
SESSION_DB = os.environ.get("SESSION_DB", "server/nao.db")

# Tracing (SDK reads OPENAI_AGENTS_DISABLE_TRACING; we keep it on by default)
OPENAI_AGENTS_TRACE = os.environ.get("OPENAI_AGENTS_TRACE", "1") == "1"

# ───────── SAGE-CBT research layer ─────────
# Topology dispatcher. "passthrough" = existing router behavior, unchanged.
# Other values activate the SAGE-CBT topology layer for the therapist subgraph.
SAGE_TOPOLOGY = os.environ.get("SAGE_TOPOLOGY", "passthrough")  # passthrough|supervisor_veto|debate|shared_pool

# SafetyAgent provider. Only used when SAGE_TOPOLOGY != "passthrough".
SAGE_SAFETY_PROVIDER = os.environ.get("SAGE_SAFETY_PROVIDER", "openai")  # openai|claude

# Models for the SafetyAgent (each provider picks the right key).
SAFETY_MODEL_OPENAI = os.environ.get("SAFETY_MODEL_OPENAI", "gpt-4o")
SAFETY_MODEL_CLAUDE = os.environ.get("SAFETY_MODEL_CLAUDE", "claude-opus-4-7")

# Anthropic key — optional. If SAGE_SAFETY_PROVIDER=claude, this MUST be set.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Per-session log directory for topology traces (written as JSONL).
SAGE_LOG_DIR = os.environ.get("SAGE_LOG_DIR", "logs")
