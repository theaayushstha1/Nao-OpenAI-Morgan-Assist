import os
import tempfile
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("PINECONE_API_KEY", "pc-test")
# Force OPEN auth mode for the default suite. The real value lives in .env
# and would otherwise 401 every test that doesn't send X-NAO-Secret.
# test_security.py monkeypatches the config attribute when it needs auth on.
os.environ["NAO_SHARED_SECRET"] = ""
# Tempfile rather than ":memory:" because memory.py opens a new sqlite
# connection per call; ":memory:" gives each connection its own empty DB
# and FK inserts across helpers fail.
_db_fd, _db_path = tempfile.mkstemp(suffix=".db", prefix="nao-test-")
os.close(_db_fd)
os.environ.setdefault("SESSION_DB", _db_path)
