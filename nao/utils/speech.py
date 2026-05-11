# -*- coding: utf-8 -*-
"""
Central speech module for NAO robot.
Provides phrase pools, expressive TTS, animated speech, and conversational helpers.
Python 2.7 compatible.
"""
from __future__ import print_function
import random
import time

# ---------------------------------------------------------------------------
# Phrase pools — 4-6 variants each, {name} placeholders where appropriate
# ---------------------------------------------------------------------------

PHRASE_POOLS = {
    "greeting_known": [
        "Hello, {name}! It's wonderful to see you again.",
        "Welcome back, {name}! How have you been?",
        "{name}, it's always a pleasure to see you.",
        "Good to see you, {name}. How have things been?",
        "Welcome back, {name}. I'm glad you're here.",
    ],
    "greeting_unknown": [
        "Hello! I don't believe we've been introduced. My name is NAO.",
        "Welcome! My name is NAO. May I ask yours?",
        "Hello there! I'm NAO, and it's a pleasure to meet you.",
        "Hi! I'm NAO. I'd love to get to know you.",
    ],
    # nao-therapy: entering_chat / entering_chatbot / entering_mininao
    # pools removed — those agents are unimported on this branch. The
    # therapy entry pool stays.
    "entering_therapist": [
        "I'm here to listen and support you.",
        "This is a safe and confidential space. Take your time.",
        "I'm here for you, whenever you're ready to talk.",
        "Take your time. I'm right here with you.",
    ],
    "farewell": [
        "It was a pleasure, {name}. Take care!",
        "Until next time, {name}. Have a wonderful day!",
        "Goodbye, {name}. I really enjoyed our conversation.",
        "I look forward to speaking with you again, {name}.",
        "Take care, {name}! See you soon.",
    ],
    "farewell_therapist": [
        "Take good care of yourself, {name}.",
        "You showed real strength today, {name}. I'm proud of you.",
        "Thank you for talking with me today, {name}. Be kind to yourself.",
        "Remember, {name}, it takes real courage to share. I'm proud of you.",
        "Until next time, {name}. You're doing better than you think.",
    ],
    "processing": [
        # Short — under 1.5s spoken. Used as the first filler so it doesn't
        # talk over the agent's reply when it lands quickly.
        "Hmm.",
        "Let me see.",
        "One sec.",
        "Thinking.",
        "Good question.",
        "Right.",
        "Okay.",
        "Mm-hmm.",
        "Got it.",
        "Interesting.",
        # Mid — 2-3s. Used if the reply is taking a bit longer.
        "Let me think about that.",
        "Give me a moment.",
        "I'm working on that.",
        "Just a sec.",
        "Hold on a moment.",
        "Bear with me.",
        "Let me consider that.",
        "Thinking it through.",
        "Processing that.",
        "Let me work that out.",
        "Mulling it over.",
        "Turning that over.",
        "Sitting with that.",
        "Let me put that together.",
        "Pulling that up now.",
        "Just a second, please.",
        "I'm chewing on that.",
        "Let me figure this out.",
        "Working on a good answer.",
        "Sorting through that.",
        # Conversational / warmer — feels less robotic.
        "That's a good one, let me think.",
        "Hmm, give me a moment.",
        "Okay, working on it.",
        "Alright, let me see.",
        "Great question. One sec.",
        "Let me wrap my head around that.",
        "Thinking that through with you.",
        "Hold that thought.",
        "Let me check on that.",
        "I'm on it.",
        "Sit tight a sec.",
        "Just a heartbeat.",
        "Looking into it.",
        "Cooking something up.",
        "Putting it together now.",
        # Therapy-friendly: gentle, non-clinical pacing words.
        "Taking that in.",
        "Let me sit with that.",
        "I hear you, give me a moment.",
        "That's worth thinking about.",
        "Let me find the right words.",
        "Holding that with you.",
        "Let me reflect on that.",
        # Slightly playful for chat mode.
        "Hmm, my circuits are turning.",
        "Cogs are spinning.",
        "Loading thoughts.",
        "Rendering an answer.",
        "Let me line that up.",
        "Crunching it now.",
        "Wheels are turning.",
        "Almost there.",
        "Coming together.",
        "Putting it in order.",
    ],
    "error_connection": [
        "I'm experiencing a brief connection issue. Could we try that again?",
        "It seems the connection was interrupted. Let me try once more.",
        "I encountered a connection issue. Shall we try again?",
        "I had difficulty reaching the server. One more attempt?",
    ],
    "error_not_heard": [
        "I didn't quite catch that. Could you say it again?",
        "I apologize, I didn't hear that clearly. Could you repeat it?",
        "I couldn't hear you clearly. Could you repeat that?",
        "I wasn't able to pick that up. Would you mind saying it again?",
    ],
    "error_not_understood": [
        "I'm not quite sure how to respond to that. Could you rephrase?",
        "Could you try phrasing that a different way?",
        "I'm having difficulty with that one. Could you elaborate?",
        "I want to give you a good answer. Could you ask that differently?",
    ],
    "error_general": [
        "Let me try that again.",
        "I encountered a small issue. Let me try once more.",
        "Something didn't go as expected. Let me try again.",
        "My apologies. Let me give that another attempt.",
    ],
    "acknowledgment": [
        "Thank you for sharing that.",
        "I hear you. Thank you.",
        "That's very insightful. Thank you for sharing.",
        "I appreciate you telling me that.",
        "I understand. Thank you for telling me.",
    ],
    "mood_sad": [
        "I can sense you're feeling down, {name}. I'm right here with you.",
        "It sounds like things are tough right now, {name}. I'm here to listen.",
        "I hear the sadness in your words, {name}. You don't have to face this alone.",
        "It's okay to feel this way, {name}. I'm not going anywhere.",
    ],
    "mood_happy": [
        "You sound wonderful today, {name}! That truly makes me happy to hear.",
        "I love hearing that positivity, {name}. It's truly uplifting.",
        "That's wonderful, {name}! Your energy is contagious.",
        "You're in a wonderful place, {name}. I'd love to hear more about what's going well.",
    ],
    "mood_angry": [
        "I can hear the frustration, {name}. Your feelings are completely valid.",
        "It sounds like something really got to you, {name}. I'm listening.",
        "I understand you're upset, {name}. Let's talk through it together.",
        "Your frustration makes sense, {name}. I'm here for you.",
    ],
    "mood_stressed": [
        "It sounds like you're carrying a lot right now, {name}.",
        "That does sound stressful, {name}. Take a deep breath with me.",
        "I can tell there's a lot on your plate, {name}. Let's take it one step at a time.",
        "You're dealing with a lot, {name}. It's okay to pause and breathe.",
    ],
    "mood_calm": [
        "You seem at peace today, {name}. That's really wonderful.",
        "I can tell you're in a good headspace, {name}. That's great to hear.",
        "You sound relaxed today, {name}. What a lovely way to be.",
        "There's a nice calm about you today, {name}.",
    ],
    "posture_done": [
        "There we go.",
        "All done.",
        "How does that feel?",
        "All set.",
    ],
    "dance_intro": [
        "Watch this!",
        "Allow me to demonstrate my moves!",
        "Here we go! Watch closely.",
        "I've been looking forward to this!",
    ],
    "dance_followup": [
        "How was that?",
        "Not bad for a robot, right?",
        "I've been working on that one!",
        "I hope you enjoyed that!",
    ],
    "ask_name": [
        # Warmer, conversational, single-prompt — covers face-learning context
        # ("look at me") and name in one breath so we don't say two prompts.
        "Hey there. Before we get going, what should I call you? Just look at me when you say it.",
        "Hi! I'd love to remember you for next time. What's your name? Just face me as you say it.",
        "Quick intro — what's your name? Look this way so I can remember your face too.",
        "Before we dive in, what should I call you? Look at me so I can put a face to the name.",
        "Hey! What's your name? Face me when you answer so I'll know you next time.",
    ],
    "ask_name_retry": [
        "Sorry, didn't catch that — what was your name?",
        "One more time, what should I call you?",
        "I missed that. Your name?",
    ],
    "listening_cue": [
        "Mm-hmm.",
        "I see.",
        "Please, go on.",
        "I'm listening.",
        "I understand.",
    ],
    "filler": [
        "Hmm, ",
        "Well, ",
        "Let me see, ",
        "That's a good point, ",
    ],
}


# ---------------------------------------------------------------------------
# Expressive style presets  (speed%, pitch%)
# ---------------------------------------------------------------------------

STYLES = {
    "warm":       {"speed": 90,  "pitch": 95},
    "excited":    {"speed": 110, "pitch": 110},
    "calm":       {"speed": 80,  "pitch": 90},
    "thinking":   {"speed": 85,  "pitch": 95},
    "empathetic": {"speed": 85,  "pitch": 90},
    "neutral":    {"speed": 100, "pitch": 100},
}


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def random_phrase(category, **kwargs):
    """Pick a random phrase from *category* and format with **kwargs."""
    pool = PHRASE_POOLS.get(category, [])
    if not pool:
        return ""
    phrase = random.choice(pool)
    if kwargs:
        try:
            return phrase.format(**kwargs)
        except (KeyError, IndexError):
            return phrase
    return phrase


def time_of_day_greeting(name=None):
    """Return 'Good morning/afternoon/evening, {name}!' based on local time."""
    hour = time.localtime().tm_hour
    if hour < 12:
        part = "Good morning"
    elif hour < 17:
        part = "Good afternoon"
    else:
        part = "Good evening"
    if name:
        return "{}, {}!".format(part, name)
    return "{}!".format(part)


def add_filler(text, probability=0.3):
    """Randomly prepend a conversational filler to *text*."""
    if not text:
        return text
    if random.random() < probability:
        filler = random.choice(PHRASE_POOLS.get("filler", ["Well, "]))
        # Lowercase the first character of text after filler
        return filler + text[0].lower() + text[1:]
    return text


# ---------------------------------------------------------------------------
# TTS tag helpers
# ---------------------------------------------------------------------------

def format_expressive(text, style="neutral"):
    """Wrap *text* with naoqi TTS tags for speed and pitch. Returns tagged string."""
    s = STYLES.get(style, STYLES["neutral"])
    prefix = "\\rspd={speed}\\ \\vct={pitch}\\".format(speed=s["speed"], pitch=s["pitch"])
    suffix = "\\rspd=100\\ \\vct=100\\"
    return "{} {} {}".format(prefix, text, suffix)


def expressive_say(tts, text, style="neutral"):
    """Speak *text* with expressive TTS tags applied."""
    tagged = format_expressive(text, style)
    try:
        tts.say(tagged)
    except Exception as e:
        print("[expressive_say error]: {}".format(e))
        try:
            tts.say(text)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# ALAnimatedSpeech helpers
# ---------------------------------------------------------------------------

def animated_say(session, text, fallback_tts=None):
    """Use ALAnimatedSpeech to speak with auto-gestures.
    Falls back to plain tts.say() if ALAnimatedSpeech is unavailable.

    Args:
        session: qi.Session (connected).
        text: string to speak.
        fallback_tts: ALTextToSpeech proxy to use as fallback.
    """
    try:
        anim_speech = session.service("ALAnimatedSpeech")
        anim_speech.say(text)
    except Exception as e:
        print("[animated_say fallback]: {}".format(e))
        if fallback_tts:
            try:
                fallback_tts.say(text)
            except Exception:
                pass


def animated_expressive_say(session, text, style="neutral", fallback_tts=None):
    """Combined animated speech with expressive TTS tags."""
    tagged = format_expressive(text, style)
    animated_say(session, tagged, fallback_tts=fallback_tts)


# ---------------------------------------------------------------------------
# Listening cue
# ---------------------------------------------------------------------------

def listening_cue(tts, probability=0.15):
    """Emit a brief acknowledgment cue with the given probability."""
    if random.random() < probability:
        cue = random_phrase("listening_cue")
        if cue:
            try:
                tts.say(cue)
            except Exception:
                pass
