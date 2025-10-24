# -*- coding: utf-8 -*-
from naoqi import ALProxy
import requests, json, time, random, re, threading
from processing_announcer import ProcessingAnnouncer


SERVER_URL = "http://172.20.95.123:5000/upload"
TIMEOUT = 20


try:
    unicode
except NameError:
    unicode = str


# ===== EXIT DETECTION SYSTEM =====
EXIT_PATTERNS = [
    # Direct exit commands
    r"\b(exit|quit|stop|end|goodbye|bye|close)\b.*\b(chat|mode|conversation|talking|session)\b",
    r"\b(chat|mode|conversation|talking|session)\b.*\b(exit|quit|stop|end|goodbye|bye|close)\b",
    
    # Standalone exit phrases
    r"^(exit|quit|stop now|end chat|goodbye|bye bye|that's all|that is all)$",
    r"^(i'm done|i am done|we're done|we are done)$",
    r"^(stop talking|stop listening|no more)$",
    
    # Polite exit phrases
    r"\b(i (want|need) to (go|leave|stop)|let me (go|leave)|gotta go)\b",
    r"\b(talk to you later|catch you later|see you later)\b",
    r"\b(thanks.*bye|thank you.*bye|thanks.*good(bye)?)\b",
    
    # Context-specific exits
    r"\b(stop.*mode|exit.*mode|leave.*mode|quit.*mode)\b",
    r"\b(go back|return|switch back)\b.*\b(wake|main|menu)\b",
]


EXIT_KEYWORDS = [
    "exit", "quit", "stop", "end", "goodbye", "bye", "close",
    "done", "finished", "that's all", "no more", "leave", "go back"
]


def _detect_exit_intent(text):
    """
    Analyze user input for exit intent using pattern matching.
    Returns: True if exit detected, False otherwise
    """
    if not text:
        return False
    
    text_lower = text.lower().strip()
    
    # Check regex patterns first (most accurate)
    for pattern in EXIT_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            print("[EXIT DETECTED] Pattern match: {}".format(pattern))
            return True
    
    # Check for standalone exit keywords in short utterances
    words = text_lower.split()
    if len(words) <= 3:  # Short utterances
        for keyword in EXIT_KEYWORDS:
            if keyword in words:
                print("[EXIT DETECTED] Keyword: {}".format(keyword))
                return True
    
    return False


def get_available_gestures(behav_mgr):
    """Load built-in gestures only"""
    try:
        all_behaviors = behav_mgr.getInstalledBehaviors()
    except:
        return []
    gestures = [b for b in all_behaviors if "animations/Stand/Gestures/" in b]
    key = ["ShowSky", "Explain", "This", "YouKnowWhat", "Point", "Think", "Yes", "No", "ComeOn", "Shrug"]
    priority = [g for g in gestures if any(k in g for k in key)]
    if len(priority) < 10:
        priority += random.sample(gestures, min(len(gestures), 10 - len(priority)))
    pool = sorted(list(set(priority)))
    print("[Info] Loaded {} built-in gestures".format(len(pool)))
    return pool


def _split_sentences(t):
    return [p.strip() for p in re.split(r'(?<=[.!?]) +', t) if p.strip()]


def _loop_gestures(behav_mgr, pool, stop_flag):
    last = None
    while not stop_flag.is_set() and pool:
        try:
            g = random.choice([x for x in pool if x != last] or pool)
            last = g
            print("[Gesture]", g)
            if behav_mgr.isBehaviorRunning(g):
                behav_mgr.stopBehavior(g)
            behav_mgr.runBehavior(g)
            time.sleep(random.uniform(0.8, 1.5))
        except Exception as e:
            print("[Gesture Err]", str(e))
            time.sleep(0.5)


def _safe_say(tts, behav_mgr, text, pool):
    try:
        parts = _split_sentences(text) or [text]
        for p in parts:
            stop = threading.Event()
            t = threading.Thread(target=_loop_gestures, args=(behav_mgr, pool, stop))
            t.daemon = True
            t.start()
            time.sleep(0.05)
            print("[Say]", p)
            if isinstance(p, unicode):
                tts.say(p.encode('utf-8'))
            else:
                tts.say(p)
            stop.set()
            t.join(timeout=0.1)
            try:
                behav_mgr.stopAllBehaviors()
            except:
                pass
            time.sleep(0.1)
    except Exception as e:
        print("[Say Err]", str(e))
        try:
            tts.say("Okay.")
        except:
            pass


def with_processing_announcer(tts, func):
    ann = ProcessingAnnouncer(tts_say=tts.say, stop_all=getattr(tts, "stopAll", None),
                              first_delay=2.0, interval=3.0, max_utterances=2)
    try:
        ann.start()
        return func()
    finally:
        try:
            ann.stop(interrupt=True)
        except:
            pass


def chatbot_mode(record_audio_func, tts, nao_ip="127.0.0.1", nao_port=9559):
    print("🧠 Morgan Chatbot Mode")
    behav_mgr, pool = None, []
    try:
        behav_mgr = ALProxy("ALBehaviorManager", nao_ip, nao_port)
        pool = get_available_gestures(behav_mgr)
    except Exception as e:
        print("[Warn]", str(e))

    _safe_say(tts, behav_mgr, "You may now ask me a question about Morgan.", pool)

    while True:
        try:
            audio = record_audio_func()
            print("[User spoke]", audio)

            def call():
                with open(audio, 'rb') as f:
                    return requests.post(
                        SERVER_URL,
                        files={'file': f},
                        data={'username': 'friend', 'mode': 'chatbot'},
                        timeout=TIMEOUT
                    )

            res = with_processing_announcer(tts, call)
            if res.status_code != 200:
                _safe_say(tts, behav_mgr, "Sorry, I couldn't understand that.", pool)
                continue

            data = res.json() or {}
            user_input = (data.get("user_input") or "").strip()
            reply = (data.get("reply") or "").strip()
            
            print("[USER INPUT] {}".format(user_input))
            
            # ===== CHECK FOR EXIT INTENT =====
            if _detect_exit_intent(user_input):
                _safe_say(tts, behav_mgr, "Understood. Exiting chatbot mode. See you later!", pool)
                print("👋 User requested exit")
                try:
                    behav_mgr.stopAllBehaviors()
                except:
                    pass
                break
            
            # ===== SPEAK RESPONSE =====
            if not reply:
                _safe_say(tts, behav_mgr, "I couldn't find anything useful.", pool)
            else:
                _safe_say(tts, behav_mgr, reply, pool)

            try:
                behav_mgr.stopAllBehaviors()
            except:
                pass
            print("🎤 Ready for next question...")

        except KeyboardInterrupt:
            print("👋 Keyboard interrupt")
            _safe_say(tts, behav_mgr, "Exiting chatbot mode.", pool)
            try:
                behav_mgr.stopAllBehaviors()
            except:
                pass
            break

        except Exception as e:
            print("[Err]", str(e))
            _safe_say(tts, behav_mgr, "Something went wrong.", pool)
