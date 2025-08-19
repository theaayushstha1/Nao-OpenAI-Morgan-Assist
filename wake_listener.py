# wake_listener.py
# -*- coding: utf-8 -*-
"""
Listen for voice commands to wake NAO or trigger simple actions.
Avoids repeated triggers by debouncing and flushes recognizer after each match.
Returns "chat" when the user says a chat-related wake command.
"""

from naoqi import ALProxy
import time
import random

DEBOUNCE_SECONDS = 2.0

def listen_for_command(nao_ip, port=9559):
    tts    = ALProxy("ALTextToSpeech",      nao_ip, port)
    asr    = ALProxy("ALSpeechRecognition", nao_ip, port)
    memory = ALProxy("ALMemory",            nao_ip, port)

    # Extended vocab for flexibility
    vocab = [
        "nao",
        "stand up",
        "sit down",
        "let's chat",
        "let's talk",
        "talk mode",
        "start a conversation",
        "chat mode"
    ]

    try:
        asr.unsubscribe("NAO_Chat_Listener")
    except:
        pass

    asr.pause(True)
    asr.setLanguage("English")
    asr.setVocabulary(vocab, False)
    asr.pause(False)
    asr.subscribe("NAO_Chat_Listener")

    tts.say("I'm listening. Say 'NAO' to wake me.")

    last_trigger = 0

    while True:
        data = memory.getData("WordRecognized")
        if isinstance(data, list) and len(data) == 2:
            word, conf = data
            word = word.lower()
            now = time.time()

            if conf > 0.45 and word in vocab and (now - last_trigger) > DEBOUNCE_SECONDS:
                last_trigger = now

                try:
                    asr.unsubscribe("NAO_Chat_Listener")
                except:
                    pass

                print("[Heard]:", word)

                if word == "nao":
                    reply = random.choice(["Yes?", "How can I help?", "Here I am!"])
                    tts.say(reply)

                elif word == "stand up":
                    posture = ALProxy("ALRobotPosture", nao_ip, port)
                    posture.goToPosture("StandInit", 0.6)
                    tts.say("Standing up.")

                elif word == "sit down":
                    posture = ALProxy("ALRobotPosture", nao_ip, port)
                    posture.goToPosture("Sit", 0.6)
                    tts.say("Sitting down.")

                elif word in ["let's chat", "let's talk", "talk mode", "start a conversation", "chat mode"]:
                    tts.say("Okay, let’s have a chat!")
                    return "chat"

                # Resume listening
                asr.subscribe("NAO_Chat_Listener")
                time.sleep(0.2)

        time.sleep(0.1)
