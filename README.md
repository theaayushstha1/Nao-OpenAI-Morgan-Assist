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
└─ README.md

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
pip install -r requirements.txt

🚀 Quick Start
1) Clone
git clone ("My Repo Link"))
cd Nao-OpenAI-Morgan-Assist

2) Configure environment

Create a .env file (in the server directory or repo root where server.py runs):

•	OPENAI_API_KEY = sk-your-key
•	PINECONE_API_KEY = pcsk-your-key
•	PINECONE_INDEX_NAME = vectorized-datasource
•	PINECONE_NAMESPACE = docs
•	PINECONE_ENV = us-east-1
•	WHISPER_MODEL = whisper-1

# NAO defaults
NAO_IP=171.20.95.xxx
NAO_PORT=9559


3) Run the Flask backend (Python 3)
py server.py


4) Run on NAO (Python 2.7)
python main.py
# Wake phrases: "nao", "let's chat", "mini nao", "morgan assist"

🔌 API Endpoints (server)

POST /upload – multipart audio file → Whisper → { user_input, reply, active_mode, … }

POST /chat_text – JSON {username, text, mode?} → GPT (+ Pinecone when mode == "chatbot")

POST /face/recognize – multipart image → { match, name?, distance }

POST /face/enroll – multipart image + name → { ok, enrolled }

GET /face/list – summary of stored encodings

# 🧭 Modes

General – default concise helper

Study – stepwise explanations + mini practice question

Therapist – warm, validating (non-clinical) support

Broker – neutral market concepts (educational only)

Chatbot – Morgan CS knowledge via Pinecone context + GPT

The server auto-detects “switch to … mode” phrases and keeps your normal sentences intact (e.g., it won’t strip the word study from “I study algorithms”).

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




# 📜 License

**Released under the MIT License. See LICENSE**
.

## 👨‍💻 Authors

- **Dr. Shuangbao "Paul" Wang – Faculty Advisor / Principal Investigator**  
  Chairperson, Department of Computer Science, Morgan State University

- **Aayush Shrestha – Lead Developer/ Research Assistant**  
  Morgan State University, Department of Computer Science  


