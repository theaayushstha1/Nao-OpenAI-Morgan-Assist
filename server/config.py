"""Environment-driven configuration for the server."""
import os
from dotenv import load_dotenv

load_dotenv()

# OpenAI
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "gpt-4o-mini")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "gpt-4o-mini")
CHATBOT_MODEL = os.environ.get("CHATBOT_MODEL", "gpt-4o-mini")
THERAPIST_MODEL = os.environ.get("THERAPIST_MODEL", "gpt-4o")
SKILLS_MODEL = os.environ.get("SKILLS_MODEL", "gpt-4o-mini")
CRISIS_MODEL = os.environ.get("CRISIS_MODEL", "gpt-4o-mini")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "whisper-1")

# Pinecone
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
PINECONE_INDEX_NAME = os.environ.get("PINECONE_INDEX_NAME", "msu-cs-knowledge")
PINECONE_NAMESPACE = os.environ.get("PINECONE_NAMESPACE", "docs")

# Networking
NAO_IP = os.environ.get("NAO_IP", "172.20.95.111")
NAO_PORT = int(os.environ.get("NAO_PORT", "9559"))
SERVER_IP = os.environ.get("SERVER_IP", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "5000"))

# Persistence
SESSION_DB = os.environ.get("SESSION_DB", "server/nao.db")

# Tracing (SDK reads OPENAI_AGENTS_DISABLE_TRACING; we keep it on by default)
OPENAI_AGENTS_TRACE = os.environ.get("OPENAI_AGENTS_TRACE", "1") == "1"
