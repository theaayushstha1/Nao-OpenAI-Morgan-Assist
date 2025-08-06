# wake_listener.py
# -*- coding: utf-8 -*-
"""
Listen for voice commands to wake NAO or trigger simple actions.
Responds once per utterance, then resets recognition to avoid repeats.
Returns "chat" when the user says "let's chat".
"""

from naoqi import ALProxy
import time

# Minimum seconds between handling the same wake word to avoid double-triggers
DEBOUNCE_SECONDS = 1.5

def listen_for_command(nao_ip, port=9559):
    tts    = ALProxy("ALTextToSpeech",      nao_ip, port)
    asr    = ALProxy("ALSpeechRecognition", nao_ip, port)
    memory = ALProxy("ALMemory",            nao_ip, port)

    # Words we care about
    vocab = ["nao", "stand up", "sit down", "let's chat"]

    # ─── Initialize ASR ────────────────────────────────────────────────────────
    try:
        asr.unsubscribe("NAO_Chat_Listener")
    except Exception:
        pass
    asr.pause(True)
    asr.setLanguage("English")
    asr.setVocabulary(vocab, False)
    asr.pause(False)
    asr.subscribe("NAO_Chat_Listener")
    # ─────────────────────────────────────────────────────────────────────────

    tts.say("I'm listening. Please say NAO to wake me up.")

    last_trigger = 0.0

    while True:
        data = memory.getData("WordRecognized")  # [word, confidence]
        if isinstance(data, list) and len(data) == 2:
            word, conf = data
            word = word.lower()
            now = time.time()

            # Check confidence, valid word, and debounce window
            if conf > 0.4 and word in vocab and (now - last_trigger) > DEBOUNCE_SECONDS:
                last_trigger = now

                # Unsubscribe to flush the ASR buffer
                try:
                    asr.unsubscribe("NAO_Chat_Listener")
                except Exception:
                    pass
                time.sleep(0.5)

                print("[Heard]:", word)

                # Handle each command
                if word == "nao":
                    tts.say("Yes?")
                elif word == "stand up":
                    posture = ALProxy("ALRobotPosture", nao_ip, port)
                    posture.goToPosture("StandInit", 0.6)
                    tts.say("Standing up.")
                elif word == "sit down":
                    posture = ALProxy("ALRobotPosture", nao_ip, port)
                    posture.goToPosture("Sit", 0.6)
                    tts.say("Sitting down.")
                elif word == "let's chat":
                    tts.say("Okay, let's chat.")
                    return "chat"

                # Re-subscribe for the next command
                asr.subscribe("NAO_Chat_Listener")
                time.sleep(0.2)

        time.sleep(0.1)
