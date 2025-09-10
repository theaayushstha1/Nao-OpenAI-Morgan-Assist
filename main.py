# main.py
# -*- coding: utf-8 -*-
from config import NAO_IP, NAO_PORT
from naoqi import ALProxy
from wake_listener import listen_for_command
from chat_mode import enter_chat_mode
from mini_nao import enter_mini_nao_mode

def main():
    print("Starting NAO Chat System...")
    robot = ALProxy("ALTextToSpeech", NAO_IP, NAO_PORT)

    while True:
        command = listen_for_command(NAO_IP, NAO_PORT)

        if command == "chat":
            print("Entering chat mode...")
            enter_chat_mode(robot, nao_ip=NAO_IP, port=NAO_PORT)
            # when chat exits, we loop back to wake listener

        elif command == "mininao":
            print("Entering MiniNao mode...")
            enter_mini_nao_mode(nao_ip=NAO_IP, port=NAO_PORT)
            # when MiniNao exits, we loop back to wake listener

        else:
            print("Unknown command or exit trigger:", command)

if __name__ == "__main__":
    main()
