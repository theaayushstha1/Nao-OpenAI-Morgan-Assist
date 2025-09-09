# main.py
# -*- coding: utf-8 -*-
from config import NAO_IP, NAO_PORT
from naoqi import ALProxy
from wake_listener import listen_for_command
from chat_mode import enter_chat_mode

def main():
    print("Starting NAO Chat System...")
    robot = ALProxy("ALTextToSpeech", NAO_IP, NAO_PORT)

    while True:
        command = listen_for_command(NAO_IP, NAO_PORT)
        if command == "chat":
            print("Entering chat mode...")
            enter_chat_mode(robot, nao_ip=NAO_IP)
            # when chat exits, we loop and re-arm wake listener

if __name__ == "__main__":
    main()
