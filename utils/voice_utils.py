# -*- coding: utf-8 -*-

def identify_user_from_voice(audio_path):
    print("Identifying user from voice...")

    if "alex" in audio_path.lower():
        return "Alex"
    elif "maya" in audio_path.lower():
        return "Maya"
    elif "john" in audio_path.lower():
        return "John"
    else:
        return "Guest"
