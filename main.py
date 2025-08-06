# -*- coding: utf-8 -*-
"""
Launch script for NAO chat client.
"""

from config import NAO_IP, NAO_PORT
from naoqi import ALProxy
from wake_listener import listen_for_command
from chat_mode import enter_chat_mode

def main():
    # Startup banner
    print("Starting NAO Chat System...")

    # Create TTS proxy
    robot = ALProxy("ALTextToSpeech", NAO_IP, NAO_PORT)

    # Wait for the wake-word
    command = listen_for_command(NAO_IP, NAO_PORT)

    if command == "chat":
        print("Entering chat mode...")
        enter_chat_mode(robot, nao_ip=NAO_IP)

if __name__ == "__main__":
    main()
