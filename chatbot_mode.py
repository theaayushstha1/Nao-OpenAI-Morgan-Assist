# -*- coding: utf-8 -*-
from naoqi import ALProxy
import requests, json, time, random, re, threading
from processing_announcer import ProcessingAnnouncer

SERVER_URL = "http://172.20.95.120:5000/upload"
TIMEOUT = 20

try:
    unicode
except NameError:
    unicode = str


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
            time.sleep(random.uniform(0.8, 1.5))  # quicker pacing
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
            time.sleep(0.05)  # start gestures almost instantly
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
            time.sleep(0.1)  # tiny gap before next line
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
            reply = (data.get("reply") or "").strip()
            if not reply:
                _safe_say(tts, behav_mgr, "I couldn’t find anything useful.", pool)
            else:
                _safe_say(tts, behav_mgr, reply, pool)

            try:
                behav_mgr.stopAllBehaviors()
            except:
                pass
            print("🎤 Ready for next question...")

        except KeyboardInterrupt:
            print("👋 Exit")
            _safe_say(tts, behav_mgr, "Exiting chatbot mode.", pool)
            try:
                behav_mgr.stopAllBehaviors()
            except:
                pass
            break

        except Exception as e:
            print("[Err]", str(e))
            _safe_say(tts, behav_mgr, "Something went wrong.", pool)
