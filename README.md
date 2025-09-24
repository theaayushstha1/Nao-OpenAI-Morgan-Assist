Nao-OpenAI-Morgan-Assist 🤖
📌 Overview

Nao-OpenAI-Morgan-Assist is a project that integrates the NAO humanoid robot with OpenAI GPT and Pinecone to create an intelligent assistant for the Morgan State University Computer Science Department.

The system allows the NAO robot to:

🎤 Listen to speech input

📝 Transcribe audio using OpenAI Whisper

📂 Retrieve knowledge from Pinecone (MSU CS knowledge base)

💡 Generate replies with GPT

🔊 Speak responses back through NAO’s text-to-speech

This project was developed by Aayush Shrestha.

✨ Features

🗣 Voice Interaction – Record and transcribe audio with Whisper

🧠 Chatbot Mode – Provides answers based on Morgan CS department knowledge

📚 Study Mode – Step-by-step teaching with examples and questions

💬 General Mode – Friendly Q&A and general assistance

👤 Face Recognition – Enroll and recognize users with stored encodings

🔐 Memory Manager – Saves and recalls chat history and user data

🗂 Project Structure
main.py             # Entry point – listens for wake commands (chat, mininao, chatbot)
chatbot_mode.py     # Handles chatbot mode: NAO → Whisper → Server → GPT/Pinecone → NAO
server.py           # Flask backend: Whisper, GPT, Pinecone, and Face APIs
wake_listener.py    # Wake word detection for NAO
audio_handler.py    # Handles recording and audio saving
memory_manager.py   # Stores chat history and user profiles
face_store.py       # Manages face encodings and enrolled users

⚙️ Requirements

Python 2.7 (for NAO robot scripts)

Python 3.9+ (for the Flask backend server)

NAOqi SDK

Flask

OpenAI Python SDK

Pinecone client

face_recognition

📦 Install all dependencies:

pip install -r requirements.txt

🚀 Setup Instructions

Clone the repository

git clone https://github.com/theaayushstha1/Nao-OpenAI-Morgan-Assist.git
cd Nao-OpenAI-Morgan-Assist


Create a .env file and add the following:

OPENAI_API_KEY=sk-your-key
PINECONE_API_KEY=pcsk-your-key
PINECONE_INDEX_NAME=vectorized-datasource
PINECONE_NAMESPACE=docs
NAO_IP=192.168.xx.xx
NAO_PORT=9559


Start the Flask backend server

python server.py


Run the NAO client

python main.py

🎥 Demo
<img width="629" height="390" alt="Screenshot 2025-09-24 144932" src="https://github.com/user-attachments/assets/d00f80b9-477a-45ef-ad1d-456f5b161d04" />


📜 License

This project is licensed under the MIT License.

👨‍💻 Developer

Aayush Shrestha – Lead Developer
