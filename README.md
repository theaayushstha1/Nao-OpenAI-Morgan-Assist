# 🤖 Nao-OpenAI-Morgan-Assist

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](#-license)
[![Python](https://img.shields.io/badge/Python-2.7_|_3.2+-blue.svg)](#-requirements)
[![Platform](https://img.shields.io/badge/Robot-NAO-orange.svg)]()
[![OpenAI](https://img.shields.io/badge/API-OpenAI-black.svg)]()
[![Pinecone](https://img.shields.io/badge/VectorDB-Pinecone-5B9BD5.svg)]()

A voice-driven assistant that connects the **NAO humanoid robot** to **OpenAI (Whisper + GPT)** and a **Pinecone** knowledge base for the **Morgan State University (MSU) Computer Science Department**.

![nao](https://github.com/user-attachments/assets/826d4b7b-7c11-4712-8d5c-7a1b1829ccff)

---

## 📌 Overview
**Nao-OpenAI-Morgan-Assist** lets NAO:
- 🎤 **Listen** to users
- 📝 **Transcribe** speech with OpenAI **Whisper**
- 📂 **Retrieve** Morgan CS knowledge from **Pinecone**
- 💡 **Generate** answers with **GPT**
- 🔊 **Speak** replies via NAO TTS

> Developed by **Aayush Shrestha** under the supervision of **Dr. Shuangbao "Paul" Wang**.

---

## ✨ Features
- 🧠 **OpenAI Agents SDK** – Multi-agent routing (router + chat, chatbot, skills, therapist with CBT/grounding sub-agents)
- 🗣 **Voice + Vision** – Whisper STT, GPT-4o multimodal (face emotion read from per-turn JPEG)
- 📚 **Morgan CS RAG** – Pinecone retrieval for department knowledge
- 💙 **Therapy Mode** – Empathetic companion with CBT thought records, grounding exercises, per-session emotion logging, cross-session recaps
- 🛡 **Safety Gate** – Pre-dispatch crisis check (keyword + LLM) with 988 hotline fallback
- 👤 **Face Recognition** – On-robot naoqi ALFaceDetection for user recall
- 💾 **SQLiteSession** – SDK-managed conversation history + camera consent + therapy recaps
- 🤖 **NAO Actions** – 18 tools (pose, gesture, move, dance, LEDs) captured into action queue for in-order execution

---

## 🗂 Project Structure

```
Nao-OpenAI-Morgan-Assist/
├─ nao/                           # Python 2.7 — deploy this to the robot
│   ├─ main.py                    # Wake loop entry
│   ├─ wake_listener.py           # Wake phrase + hint extraction
│   ├─ conversation.py            # Single loop: record → POST /turn → speak + execute
│   ├─ audio_handler.py           # VAD + recording
│   ├─ processing_announcer.py    # Background "please wait"
│   ├─ config.py                  # IPs, ports
│   └─ utils/
│       ├─ camera_capture.py      # snap_quick() for per-turn JPEG
│       ├─ nao_execute.py         # Dispatches server actions to naoqi
│       ├─ face_naoqi.py          # Face reco/learning
│       ├─ ask_name_utils.py      # Name ask flow
│       ├─ exit_detection.py
│       ├─ name_utils.py
│       └─ speech.py              # Phrase pools + expressive TTS
├─ server/                        # Python 3.11+ Flask + OpenAI Agents SDK
│   ├─ server.py                  # POST /turn + GET /health
│   ├─ safety.py                  # Pre-dispatch crisis gate
│   ├─ session.py                 # SQLiteSession + consent + recaps
│   ├─ config.py
│   ├─ agents/                    # router, chat, chatbot, skills, therapist, cbt_coach, grounding_coach
│   ├─ tools/                     # nao_actions, pinecone_search, emotion, skills_tools
│   ├─ tests/                     # pytest (34 tests)
│   └─ requirements.txt
├─ docs/
│   ├─ superpowers/               # Design specs + implementation plans
│   └─ reference/                 # HTML reference docs
├─ CLAUDE.md, README.md, LICENSE, pytest.ini
```

---

## ⚙️ Requirements
- **Python 2.7** (NAO side, NAOqi SDK only)
- **Python 3.11+** (server; `openai-agents`, `openai>=1.50`, `flask`, `pinecone-client`)

## 🚀 Quick Start

### 1) Server (Python 3.11+)
```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` at repo root:
```
OPENAI_API_KEY=sk-your-key
PINECONE_API_KEY=pcsk-your-key
PINECONE_INDEX_NAME=msu-cs-knowledge
PINECONE_NAMESPACE=docs
NAO_IP=172.20.95.111
SERVER_IP=0.0.0.0
```

Run the server:
```bash
python -m server.server        # dev
# or: gunicorn -w 1 -b 0.0.0.0:5000 server.server:app
```

### 2) NAO (Python 2.7)
Copy the `nao/` folder to the robot:
```bash
scp -r nao/ nao@<nao-ip>:/home/nao/nao_assist/
ssh nao@<nao-ip>
export SERVER_IP=<server-host>
python /home/nao/nao_assist/main.py
```

Wake phrases: "nao", "hey nao", or with hints: "morgan assist", "therapy", "mini nao".

### 3) Run tests
```bash
python -m pytest
# 34 tests pass
```

## 🔌 API

**POST `/turn`** (multipart):
- `audio` (WAV), `image` (JPEG, optional), `username`, `hint` (`chat`|`morgan`|`therapy`|`skills`), `end_session` (bool)

**Response JSON:**
```json
{ "username": "alice", "user_input": "...", "reply": "...",
  "active_agent": "therapist",
  "actions": [{"name":"change_eye_color","args":{"color":"blue"}}],
  "crisis": false, "suppress_image": false }
```

`actions[]` is the ordered list NAO executes. Router automatically hands off to the right specialist when `hint` is null.

## 🧭 Agents

- **router** — triage
- **chat** — general chat + NAO actions
- **chatbot** — Morgan CS RAG via Pinecone
- **skills** — time, weather, timers, todos
- **therapist** — empathetic + CBT/grounding handoffs + vision-based emotion read
- **cbt_coach** — thought records, distortion ID, reframes
- **grounding_coach** — 5-4-3-2-1, box breathing, body scan

## 🛠 Configuration Tips

Latency tuning (speech end detection): tweak in audio_handler.py

TRAIL_MS (silence tail), POLL_MS, ENERGY_MIN_START/KEEP

Interrupt while speaking (chat mode): user can say “stop / skip / next”; the client listens in a side thread and calls tts.stopAll()

Prevent self-hearing: during TTS, temporarily lower input sensitivity or gate by energy threshold; client already filters short clips and uses brief listen windows for interrupts.

# ❓ FAQ

**Why did it not use Pinecone?**
Make sure mode is chatbot in the request (client sets this when entering “Morgan Assist”). The server path if mode == "chatbot": performs embed → Pinecone → GPT with context.

**Where do I add MSU CS docs?**
Ingest your content into the Pinecone index named by PINECONE_INDEX_NAME/PINECONE_NAMESPACE using the same embedding model (text-embedding-3-small).

**It stops recording too fast/too slow.**
Adjust TRAIL_MS, NO_SPEECH_TIMEOUT_S, and thresholds in audio_handler.py.

# 🖼 System Diagram

<img width="673" height="416" alt="Screenshot 2025-09-24 at 6 46 12 PM" src="https://github.com/user-attachments/assets/cd670690-4899-46f9-a2f5-666549661cb3" />


# Technology Stack

<img width="642" height="422" alt="Screenshot 2025-09-24 at 6 46 37 PM" src="https://github.com/user-attachments/assets/465c83b4-ebcc-461c-bea0-0fe9b5ad7b14" />



# Server health
curl http://localhost:5000/health
# => {"ok":true}

## 📜 License

**Released under the MIT License. See LICENSE**
.

.

## 👨‍💻 Authors

- **Dr. Shuangbao "Paul" Wang – Faculty Advisor / Principal Investigator**  
  Chairperson, Department of Computer Science, Morgan State University

- **Aayush Shrestha – Lead Developer/ Research Assistant**  
  Morgan State University, Department of Computer Science  


