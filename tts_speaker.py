# -*- coding: utf-8 -*-
from naoqi import ALProxy

def speak(nao_ip, text):
    tts = ALProxy("ALTextToSpeech", nao_ip, 9559)
    print("Saying:", text)
    tts.say(text)
