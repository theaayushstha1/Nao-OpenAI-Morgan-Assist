# Nao-OpenAI-Morgan-Assist

## Project Overview

NAO humanoid robot assistant integrating OpenAI (Whisper + GPT-4o) with Pinecone vector database for Morgan State University. The system supports multiple conversation modes with face recognition, gesture animations, and personalized memory.

## Architecture

```
NAO Robot (Python 2.7 / naoqi SDK)
  wake_listener.py  ->  Detects voice commands
    chat_mode.py       General conversation + gestures + dance
    chatbot_mode.py    Morgan State CS department Q&A (RAG)
    therapist_mode.py  Empathetic therapy support
    mini_nao.py        Utility skills (time, weather, timers, todos)

Flask Server (Python 3 / server.py)
  POST /upload          Audio transcription + GPT response
  POST /therapist_chat  Therapy mode endpoint
  gpt_handler.py        OpenAI ChatCompletion with tool calling
  memory_manager.py     Per-user JSON conversation persistence
```

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point - routes wake commands to modes |
| `server.py` | Flask server - Whisper transcription, GPT, Pinecone RAG |
| `gpt_handler.py` | OpenAI chat completions with robot action tools |
| `memory_manager.py` | Per-user conversation history (JSON) |
| `config.py` | Environment-based configuration |
| `chat_mode.py` | General chat with face recognition and gestures |
| `chatbot_mode.py` | Morgan State CS chatbot with RAG |
| `therapist_mode.py` | Therapy mode with mood detection |
| `mini_nao.py` | Utility skills (time, weather, timers, reminders, todos) |
| `wake_listener.py` | Voice command detection and routing |
| `audio_handler.py` | NAO audio recording with VAD |
| `processing_announcer.py` | Background "please wait" announcements |
| `face_store.py` | Face encoding storage (JSON) |
| `utils/exit_detection.py` | Shared exit intent detection |
| `utils/name_utils.py` | Shared name extraction from speech |
| `utils/face_naoqi.py` | Shared face recognition/learning via naoqi |
| `utils/ask_name_utils.py` | Shared ask-name-via-audio flow |
| `utils/camera_capture.py` | NAO camera photo capture |
| `utils/face_utils.py` | NAO face detection helpers |
| `utils/file_utils.py` | Timestamped filename generation |
| `utils/with_announcer.py` | Server call wrapper with announcements |

## Development Guidelines

- **NAO-side scripts** (main.py, chat_mode.py, chatbot_mode.py, therapist_mode.py, mini_nao.py, wake_listener.py, audio_handler.py, utils/): Must be **Python 2.7 compatible** (use `from __future__ import print_function`, no f-strings, `str.format()` only)
- **Server-side scripts** (server.py, gpt_handler.py, memory_manager.py): **Python 3**
- All IPs/ports read from environment variables or `config.py` - never hardcode
- OpenAI SDK: v1.x+ (use `openai.OpenAI()` client pattern)
- Chat history trimmed to last 50 messages per user
