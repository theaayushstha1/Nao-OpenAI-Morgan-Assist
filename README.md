# 🤖 Nao-OpenAI-Morgan-Assist

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](#-license)
[![Python](https://img.shields.io/badge/Python-2.7_|_3.9+-blue.svg)](#-requirements)
[![Platform](https://img.shields.io/badge/Robot-NAO-orange.svg)]()
[![OpenAI](https://img.shields.io/badge/API-OpenAI-black.svg)]()
[![Pinecone](https://img.shields.io/badge/VectorDB-Pinecone-5B9BD5.svg)]()

A voice-driven assistant that connects the **NAO humanoid robot** to **OpenAI (Whisper + GPT)** and a **Pinecone** knowledge base for the **Morgan State University (MSU) Computer Science Department**.

---

## 📌 Overview
**Nao-OpenAI-Morgan-Assist** lets NAO:
- 🎤 **Listen** to users
- 📝 **Transcribe** speech with OpenAI **Whisper**
- 📂 **Retrieve** Morgan CS knowledge from **Pinecone**
- 💡 **Generate** answers with **GPT**
- 🔊 **Speak** replies via NAO TTS

> Developed by **Aayush Shrestha**.

---

## ✨ Features
- 🗣 **Voice Interaction** – Robust on-device capture with silence/VAD handling  
- 🧠 **Morgan Chatbot Mode** – Answers from MSU CS knowledge base (Pinecone)  
- 📚 **Study Mode** – Teaches step-by-step with examples + quick practice  
- 💬 **General Mode** – Friendly Q&A assistant  
- 👤 **Face Recognition** – Enroll/recognize users (face encodings)  
- 💾 **Memory Manager** – Per-user chat history and name recall  
- 🧩 **Function Hooks** – Simple server “function_call” support for actions  

---

## 🗂 Project Structure
Awesome — here’s a ready-to-paste README.md in proper Markdown (titles, bold, lists, code blocks, badges, the works). Drop this straight into your repo and commit.

# 🤖 Nao-OpenAI-Morgan-Assist

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](#-license)
[![Python](https://img.shields.io/badge/Python-2.7_|_3.9+-blue.svg)](#-requirements)
[![Platform](https://img.shields.io/badge/Robot-NAO-orange.svg)]()
[![OpenAI](https://img.shields.io/badge/API-OpenAI-black.svg)]()
[![Pinecone](https://img.shields.io/badge/VectorDB-Pinecone-5B9BD5.svg)]()

A voice-driven assistant that connects the **NAO humanoid robot** to **OpenAI (Whisper + GPT)** and a **Pinecone** knowledge base for the **Morgan State University (MSU) Computer Science Department**.

---

## 📌 Overview
**Nao-OpenAI-Morgan-Assist** lets NAO:
- 🎤 **Listen** to users
- 📝 **Transcribe** speech with OpenAI **Whisper**
- 📂 **Retrieve** Morgan CS knowledge from **Pinecone**
- 💡 **Generate** answers with **GPT**
- 🔊 **Speak** replies via NAO TTS

> Developed by **Aayush Shrestha**.

---

## ✨ Features
- 🗣 **Voice Interaction** – Robust on-device capture with silence/VAD handling  
- 🧠 **Morgan Chatbot Mode** – Answers from MSU CS knowledge base (Pinecone)  
- 📚 **Study Mode** – Teaches step-by-step with examples + quick practice  
- 💬 **General Mode** – Friendly Q&A assistant  
- 👤 **Face Recognition** – Enroll/recognize users (face encodings)  
- 💾 **Memory Manager** – Per-user chat history and name recall  
- 🧩 **Function Hooks** – Simple server “function_call” support for actions  

---

## 🗂 Project Structure


Nao-OpenAI-Morgan-Assist/
├─ main.py # Entry point – wake flow (chat, mininao, chatbot)
├─ server.py # Flask backend: Whisper, GPT, Pinecone, Face APIs
├─ chatbot_mode.py # Morgan chatbot loop (NAO ⇄ Server)
├─ chat_mode.py # Multi-mode chat with gestures + interrupts
├─ audio_handler.py # Recording, VAD, trimming, pre-emphasis, AGC
├─ memory_manager.py # User profiles & chat history
├─ face_store.py # Face encodings (enroll/list/recognize)
├─ utils/
│ ├─ camera_capture.py # Take photos from NAO camera
│ └─ face_utils.py # Face detection/mood helpers
├─ requirements.txt # Python deps for the server (Py3)
├─ LICENSE # MIT
└─ README.md # You are here


---

## ⚙️ Requirements
- **Python 2.7** (NAO side scripts; NAOqi SDK)
- **Python 3.9+** (Flask backend server)
- **NAOqi SDK**
- **OpenAI** Python SDK
- **Flask**
- **Pinecone** client
- **face_recognition** (+ dlib dependencies)

Install server deps:
```bash
pip install -r requirements.txt

🚀 Quick Start
1) Clone
git clone https://github.com/theaayushstha1/Nao-OpenAI-Morgan-Assist.git
cd Nao-OpenAI-Morgan-Assist

2) Configure environment

Create a .env file (in the server directory or repo root where server.py runs):

OPENAI_API_KEY=sk-your-key
PINECONE_API_KEY=pcsk-your-key
PINECONE_INDEX_NAME=vectorized-datasource
PINECONE_NAMESPACE=docs
# Optional:
PINECONE_ENV=us-east-1
WHISPER_MODEL=whisper-1

# NAO defaults (client scripts may also read these):
NAO_IP=192.168.xx.xx
NAO_PORT=9559

3) Run the Flask backend (Python 3)
python server.py
# ⇒ serves on http://0.0.0.0:5000

4) Run on NAO (Python 2.7)
python main.py
# Wake phrases: "let's chat", "mini nao", "morgan assist"

🔌 API Endpoints (server)

POST /upload – multipart audio file → Whisper → { user_input, reply, active_mode, … }

POST /chat_text – JSON {username, text, mode?} → GPT (+ Pinecone when mode == "chatbot")

POST /face/recognize – multipart image → { match, name?, distance }

POST /face/enroll – multipart image + name → { ok, enrolled }

GET /face/list – summary of stored encodings

🧭 Modes

General – default concise helper

Study – stepwise explanations + mini practice question

Therapist – warm, validating (non-clinical) support

Broker – neutral market concepts (educational only)

Chatbot – Morgan CS knowledge via Pinecone context + GPT

The server auto-detects “switch to … mode” phrases and keeps your normal sentences intact (e.g., it won’t strip the word study from “I study algorithms”).

🛠 Configuration Tips

Latency tuning (speech end detection): tweak in audio_handler.py

TRAIL_MS (silence tail), POLL_MS, ENERGY_MIN_START/KEEP

Interrupt while speaking (chat mode): user can say “stop / skip / next”; the client listens in a side thread and calls tts.stopAll()

Prevent self-hearing: during TTS, temporarily lower input sensitivity or gate by energy threshold; client already filters short clips and uses brief listen windows for interrupts.

❓ FAQ

Why did it not use Pinecone?
Make sure mode is chatbot in the request (client sets this when entering “Morgan Assist”). The server path if mode == "chatbot": performs embed → Pinecone → GPT with context.

Where do I add MSU CS docs?
Ingest your content into the Pinecone index named by PINECONE_INDEX_NAME/PINECONE_NAMESPACE using the same embedding model (text-embedding-3-small).

It stops recording too fast/too slow.
Adjust TRAIL_MS, NO_SPEECH_TIMEOUT_S, and thresholds in audio_handler.py.

🖼 System Diagram

c:\Users\Aayush\Pictures\Screenshots\Screenshot 2025-09-24 144932.png


🧪 Minimal Test
# Server health
curl http://localhost:5000/test
# => {"message":"Test route working!"}

📜 License

Released under the MIT License. See LICENSE
.

👨‍💻 Author

Aayush Shrestha — Lead Developer