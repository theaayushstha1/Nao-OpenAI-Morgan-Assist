# -*- coding: utf-8 -*-

from config import NAO_IP, NAO_PORT
from naoqi import ALProxy
from wake_listener import listen_for_command
from chat_mode import enter_chat_mode
from mini_nao import enter_mini_nao_mode
from chatbot_mode import chatbot_mode
from audio_handler import record_audio  

def main():
    print("Starting NAO Chat System...")

    tts = ALProxy("ALTextToSpeech", NAO_IP, NAO_PORT)

    while True:
        command = listen_for_command(NAO_IP, NAO_PORT)

        if command == "chat":
            enter_chat_mode(tts, nao_ip=NAO_IP, port=NAO_PORT)
        elif command == "mininao":
            enter_mini_nao_mode(nao_ip=NAO_IP, port=NAO_PORT)
        elif command == "chatbot":
            chatbot_mode(lambda: record_audio(NAO_IP), tts)
        else:
            print("❌ Unknown command:", command)

if __name__ == "__main__":
    main()
