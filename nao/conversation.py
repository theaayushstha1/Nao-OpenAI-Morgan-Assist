# -*- coding: utf-8 -*-
"""Single conversation loop. Replaces chat_mode, chatbot_mode, therapist_mode, mini_nao."""
from __future__ import print_function

import os
import threading
import time
import requests

from naoqi import ALProxy

import config
import audio_handler
from processing_announcer import ProcessingAnnouncer
from utils import face_naoqi, ask_name_utils, nao_execute, camera_capture, exit_detection, intent as _intent
from utils import user_cache
from utils.voice_clone import clone_say
from utils.speech import expressive_say, time_of_day_greeting


_DEFAULT_TIMEOUT = 45


def _auth_headers():
    """Return X-NAO-Secret header dict (empty when OPEN mode)."""
    return {"X-NAO-Secret": config.NAO_SHARED_SECRET} if config.NAO_SHARED_SECRET else {}


def _post(wav_path, img_path, username, hint, end_session=False):
    url = "http://{0}:{1}/turn".format(config.SERVER_IP, config.SERVER_PORT)
    files = {}
    if wav_path:
        files["audio"] = open(wav_path, "rb")
    if img_path:
        files["image"] = open(img_path, "rb")
    data = {"username": username or "guest"}
    if hint:
        data["hint"] = hint
    if end_session:
        data["end_session"] = "true"
    try:
        r = requests.post(url, files=files, data=data,
                          headers=_auth_headers(), timeout=_DEFAULT_TIMEOUT)
        return r.json() if r.status_code == 200 else None
    finally:
        for f in files.values():
            f.close()


# In-process identity cache. Layered with utils.user_cache (JSON on disk) so
# identity also survives a NAO process restart. The "source" key is what the
# greet logic uses to decide whether to say "Welcome back" — see _resolve.
_USER_CACHE = {"username": None, "recognized": False, "source": None}


def _hydrate_from_disk():
    """Pull the persisted username off disk into the in-process cache.

    Called lazily on the first _resolve in this process. We only honor a
    persisted record once per process — after that, the in-memory cache is the
    source of truth so the disk file is never re-read mid-session.
    """
    if _USER_CACHE["username"]:
        return
    snapshot = user_cache.load()
    name = snapshot.get("username")
    if not name:
        return
    _USER_CACHE["username"] = name
    _USER_CACHE["recognized"] = bool(snapshot.get("recognized", False))
    _USER_CACHE["source"] = "cache_disk"


def _resolve(qi_session, tts, nao_ip):
    """Return (username, recognized, source) for this conversation turn.

    source is one of:
      cache_mem  — already known in this Python process (mid-session)
      cache_disk — pulled off the persisted JSON file (first wake post-boot)
      face       — recognized live via camera during the bridge prompt
      asked      — captured by ask_name and persisted just now
      guest      — face missed AND ask_name failed; cached for the session

    The flow guarantees we never silently stare at the user: the moment we
    decide to onboard a new person, we speak a single combined prompt that
    bridges the wake-mode transition AND the name capture, while
    ALFaceDetection runs in a background thread so a known face still gets
    picked up without a separate silent scan window.
    """
    if _USER_CACHE["username"]:
        # Same process, already resolved earlier. Switch source to cache_mem
        # so the greet logic knows this is a mid-session lookup (mode switch,
        # next wake phrase, etc.) and skips the "Welcome back" prompt.
        return _USER_CACHE["username"], _USER_CACHE["recognized"], "cache_mem"

    _hydrate_from_disk()
    if _USER_CACHE["username"]:
        # First resolve in a fresh process. Run a short face scan to confirm
        # the person in front of the camera matches the persisted identity.
        # Three possible outcomes:
        #   (a) seen matches cache  → confirm, "Welcome back"
        #   (b) seen is a DIFFERENT known name → flip cache, "Welcome back, X"
        #   (c) face visible but unrecognized → unknown stranger, RE-ONBOARD
        #       (do NOT greet them by the cached name — that was the bug)
        #   (d) no face visible at all → cached user just hasn't looked yet,
        #       silently trust cache (no false "welcome back" since we
        #       couldn't verify identity)
        try:
            seen, face_visible = face_naoqi.recognize_face_naoqi(
                qi_session, tts, subscriber_name="FaceVerify",
                timeout=1.5, return_seen=True,
            )
        except Exception as e:
            seen, face_visible = None, False
            print("[face verify error]:", e)

        if seen:
            seen_lower = seen.lower()
            if seen_lower != _USER_CACHE["username"]:
                # Different known person in frame — flip the cache to them.
                _USER_CACHE["username"] = seen_lower
                _USER_CACHE["recognized"] = True
                _USER_CACHE["source"] = "face"
                user_cache.save(seen_lower, True)
                return seen_lower, True, "face"
            # Same name — promote recognized=True since we just saw them live.
            _USER_CACHE["recognized"] = True
            return _USER_CACHE["username"], True, _USER_CACHE["source"]

        if face_visible:
            # An unknown person is standing in front of NAO. Don't greet them
            # by the cached user's name — that's the "everyone is ayush" bug.
            # Drop the cache (in-memory only; disk stays so the actual cached
            # user can still be recognized later) and fall through to the
            # full onboarding flow.
            print("[verify] unknown face in frame; cached={0!r}; re-onboarding".format(
                _USER_CACHE["username"]))
            _USER_CACHE["username"] = None
            _USER_CACHE["recognized"] = False
            _USER_CACHE["source"] = None
            return _onboard_new_user(qi_session, tts, nao_ip)

        # No face visible during the scan. Cached user probably just isn't
        # centered in frame yet. Trust the cache silently — the new
        # 'cache_unconfirmed' source tells run_streaming to skip the
        # "Welcome back" prompt since we couldn't visually confirm identity.
        return _USER_CACHE["username"], _USER_CACHE["recognized"], "cache_unconfirmed"

    return _onboard_new_user(qi_session, tts, nao_ip)


def _onboard_new_user(qi_session, tts, nao_ip):
    """Single-flow onboarding: parallel face scan + spoken bridge prompt.

    Eliminates the previous ~5s silent gap. Speaks a combined "before we
    chat I'd like to learn your face and name" prompt while a daemon thread
    runs ALFaceDetection — if a known face is in frame, we get the name for
    free and skip the audio capture step.

    The face worker honors a stop_event so it bails out promptly if ask_name
    returns fast (e.g. the user answers on the first attempt). Without the
    signal the worker would stay subscribed to ALFaceDetection for its full
    timeout and unsubscribe could race with subsequent turns.
    """
    face_result = {"name": None}
    face_stop = threading.Event()

    def _face_worker():
        try:
            n = face_naoqi.recognize_face_naoqi(
                qi_session, tts, timeout=4.0, stop_event=face_stop,
            )
            if n:
                face_result["name"] = n
        except Exception as e:
            print("[face recognize thread error]:", e)

    face_thread = threading.Thread(target=_face_worker)
    face_thread.daemon = True
    face_thread.start()

    asked = None
    try:
        asked = ask_name_utils.ask_name(
            tts, nao_ip,
            "http://{0}:{1}".format(config.SERVER_IP, config.SERVER_PORT),
            qi_session, audio_handler.record_audio,
            should_abort=lambda: bool(face_result.get("name")),
        )
    except Exception as e:
        print("[ask_name error]:", e)

    # Tell the face worker to exit and give it room to unsubscribe cleanly
    # before the next turn starts using ALFaceDetection.
    face_stop.set()
    try:
        face_thread.join(timeout=3.0)
    except Exception:
        pass

    if face_result["name"]:
        username = face_result["name"].lower()
        _USER_CACHE["username"] = username
        _USER_CACHE["recognized"] = True
        _USER_CACHE["source"] = "face"
        user_cache.save(username, True)
        return username, True, "face"

    if asked and asked != "Guest":
        try:
            face_naoqi.learn_new_face_naoqi(qi_session, tts, asked)
        except Exception:
            pass
        username = asked.lower()
        _USER_CACHE["username"] = username
        _USER_CACHE["recognized"] = False
        _USER_CACHE["source"] = "asked"
        user_cache.save(username, False)
        return username, False, "asked"

    # Onboarding failed (mic noise, name extraction missed, etc.). Cache the
    # guest fallback IN MEMORY ONLY so we don't re-ask within this session.
    # We deliberately do NOT persist guest to disk — next process boot tries
    # again from scratch.
    _USER_CACHE["username"] = "guest"
    _USER_CACHE["recognized"] = False
    _USER_CACHE["source"] = "guest"
    return "guest", False, "guest"


def _resolve_username(qi_session, tts, nao_ip):
    """Backwards-compatible 2-tuple wrapper around _resolve. The non-streaming
    `run` loop still uses the old signature; note that mode=run won't see a
    welcome-back for users restored from the disk cache, since that path
    only greets when recognized=True (set only on live face match)."""
    name, recognized, _src = _resolve(qi_session, tts, nao_ip)
    return name, recognized


def _mark_session_ended():
    """Reset the in-process cache 'source' marker (but keep the username) so
    the NEXT wake re-fires the welcome-back greeting. Without this, a user
    who said 'goodbye' and then woke NAO up again would hit cache_mem and
    get no greeting at all — silence after a wake phrase feels broken.
    """
    if _USER_CACHE.get("username") and _USER_CACHE.get("username") != "guest":
        # Demote source so the next _resolve returns cache_disk and the
        # greet block speaks "Welcome back".
        _USER_CACHE["source"] = "cache_disk"
    elif _USER_CACHE.get("username") == "guest":
        # Drop the guest fallback entirely so the NEXT session re-attempts
        # face recognition + name capture (it might succeed this time).
        _USER_CACHE["username"] = None
        _USER_CACHE["recognized"] = False
        _USER_CACHE["source"] = None


def run(qi_session, initial_hint=None):
    tts = ALProxy("ALAnimatedSpeech", config.NAO_IP, config.NAO_PORT)
    raw_tts = ALProxy("ALTextToSpeech", config.NAO_IP, config.NAO_PORT)
    motion = ALProxy("ALMotion", config.NAO_IP, config.NAO_PORT)
    posture = ALProxy("ALRobotPosture", config.NAO_IP, config.NAO_PORT)
    leds = ALProxy("ALLeds", config.NAO_IP, config.NAO_PORT)
    behav_mgr = ALProxy("ALBehaviorManager", config.NAO_IP, config.NAO_PORT)

    username, recognized = _resolve_username(qi_session, raw_tts, config.NAO_IP)
    if recognized and username != "guest":
        expressive_say(raw_tts, "Welcome back, {0}.".format(username))

    suppress_image = False
    hint = initial_hint

    while True:
        wav = audio_handler.record_audio(config.NAO_IP)
        if not wav:
            continue

        img_path = None
        if not suppress_image:
            img_path = camera_capture.snap_quick(config.NAO_IP, config.NAO_PORT)

        ann = ProcessingAnnouncer(raw_tts)
        ann.start()
        try:
            resp = _post(wav, img_path, username, hint)
        finally:
            ann.stop()
            try:
                if wav and os.path.exists(wav):
                    os.unlink(wav)
                if img_path and os.path.exists(img_path):
                    os.unlink(img_path)
            except Exception:
                pass

        hint = None

        if resp is None:
            expressive_say(raw_tts, "My brain's not responding. Let's try again.")
            continue

        if resp.get("crisis"):
            expressive_say(raw_tts, resp.get("reply") or "")
            for action in resp.get("actions") or []:
                nao_execute.run(action, qi_session, motion, posture, leds, behav_mgr, raw_tts)
            _mark_session_ended()
            break

        if resp.get("suppress_image"):
            suppress_image = True

        reply = resp.get("reply") or ""
        expressive_say(raw_tts, reply)

        for action in resp.get("actions") or []:
            nao_execute.run(action, qi_session, motion, posture, leds, behav_mgr, raw_tts)

        user_input = resp.get("user_input") or ""
        if exit_detection.detect_exit_intent(user_input):
            try:
                _post(None, None, username, None, end_session=True)
            except Exception:
                pass
            expressive_say(raw_tts, "Take care.")
            _mark_session_ended()
            break


import stream_tts


def _wait_tts_idle(memory, settle_s=0.2, timeout=0.6):
    """Block until ALTextToSpeech reports done/stopped, then a short settle.

    Timeout is short (0.6s) because OpenAI TTS plays via ALAudioPlayer (not
    ALTextToSpeech), so the status key never updates from a stale prior state
    and would otherwise pin us at the full timeout every turn.
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            status = memory.getData("ALTextToSpeech/Status")
            if isinstance(status, list) and len(status) >= 2 and status[1] in ("done", "stopped"):
                break
        except Exception:
            break
        time.sleep(0.05)
    time.sleep(settle_s)


def run_streaming(qi_session, initial_hint=None, is_mode_switch=False):
    """Streaming variant: sentences arrive over SSE and are spoken as generated.

    is_mode_switch: True when main re-entered after a mid-session "switch to X"
    instruction. The mode-switch announcement ("Switching to therapy mode.")
    has already been spoken, so we skip the welcome to avoid double-greeting.
    """
    tts = ALProxy("ALAnimatedSpeech", config.NAO_IP, config.NAO_PORT)
    raw_tts = ALProxy("ALTextToSpeech", config.NAO_IP, config.NAO_PORT)
    memory = ALProxy("ALMemory", config.NAO_IP, config.NAO_PORT)
    audio_device = ALProxy("ALAudioDevice", config.NAO_IP, config.NAO_PORT)
    motion = ALProxy("ALMotion", config.NAO_IP, config.NAO_PORT)
    posture = ALProxy("ALRobotPosture", config.NAO_IP, config.NAO_PORT)
    leds = ALProxy("ALLeds", config.NAO_IP, config.NAO_PORT)
    behav_mgr = ALProxy("ALBehaviorManager", config.NAO_IP, config.NAO_PORT)

    username, recognized, source = _resolve(qi_session, raw_tts, config.NAO_IP)

    # Greet logic. cache_mem (mid-session) is silent — the user didn't go
    # anywhere, no need to re-announce them. Mode switch is also silent
    # because main spoke the "Switching to X mode" line already.
    if is_mode_switch or source == "cache_mem":
        pass
    elif source in ("cache_disk", "face") and username != "guest":
        # Identity confirmed by either fresh disk hydration + visual match
        # ("face") OR disk hydration + same-name verification ("cache_disk").
        clone_say(raw_tts, "Welcome back, {0}.".format(username))
    elif source == "cache_unconfirmed":
        # Disk had a name but face wasn't visible to confirm. Don't risk
        # greeting a stranger by the cached user's name — say something
        # generic instead. The cache is still in-memory so subsequent turns
        # still address the user correctly downstream if it really is them.
        clone_say(raw_tts, "I'm listening.")
    elif source == "asked":
        clone_say(raw_tts, "Nice to meet you, {0}. What can I help with?".format(username))
    elif source == "guest":
        # Onboarding fell through. Don't keep retrying inside the session.
        clone_say(raw_tts, "I'm listening.")

    # Audible "go" + green eyes so the user always knows when to start.
    try:
        leds.fadeRGB("FaceLeds", 0.0, 1.0, 0.0, 0.1)  # green = listening
    except Exception:
        pass

    suppress_image = False
    hint = initial_hint
    skip_tts_wait = False
    silent_streak = 0  # count consecutive no-speech turns to re-prompt
    barge_config = {
        "enabled": config.BARGE_ENABLED,
        "threshold": config.BARGE_THRESHOLD,
        "sustain_ms": config.BARGE_SUSTAIN_MS,
        "deadzone_ms": config.BARGE_DEADZONE_MS,
        "poll_ms": config.BARGE_POLL_MS,
    }

    while True:
        if skip_tts_wait:
            skip_tts_wait = False
        else:
            _wait_tts_idle(memory, settle_s=0.4)
        wav = audio_handler.record_audio(config.NAO_IP)
        if wav is None or not wav:
            silent_streak += 1
            # After 2 consecutive silent windows, prompt the user so they
            # know NAO is still alive and waiting.
            if silent_streak == 2:
                clone_say(raw_tts, "I'm here when you're ready.")
                try:
                    leds.fadeRGB("FaceLeds", 0.0, 1.0, 0.0, 0.1)
                except Exception:
                    pass
            continue
        silent_streak = 0
        # Camera snap is opt-in (saves ~500ms per turn). Therapist agent can
        # call observe_face tool when it actually needs vision.
        img_path = None
        if config.IMAGE_PER_TURN and not suppress_image:
            img_path = camera_capture.snap_quick(config.NAO_IP, config.NAO_PORT)

        files = {}
        if wav:
            files["audio"] = open(wav, "rb")
        if img_path:
            files["image"] = open(img_path, "rb")
        data = {"username": username}
        if hint:
            data["hint"] = hint

        def handle_action(action):
            nao_execute.run(action, qi_session, motion, posture, leds, behav_mgr, raw_tts)

        def handle_done(info):
            pass

        # Acknowledge the user the moment we've captured their audio — fires
        # immediately, not after a delay. Killing the old 1.4s wait removes
        # the dead-air gap between "user finishes speaking" and "NAO reacts."
        # ProcessingAnnouncer is disabled — it created a feedback loop where
        # NAO's own "thinking" filler ("Hmm.", "Thinking it through.") got
        # recorded by the next listening window, transcribed as user input,
        # and fed back to the agent. The latency tradeoff is worth it.
        url = "http://{0}:{1}/stream_turn".format(config.SERVER_IP, config.SERVER_PORT)
        info = stream_tts.consume(
            url, files, data, raw_tts, handle_action, handle_done,
            audio_device=audio_device, barge_config=barge_config,
            memory=memory,
        )

        for f in files.values():
            f.close()
        try:
            if wav and os.path.exists(wav):
                os.unlink(wav)
            if img_path and os.path.exists(img_path):
                os.unlink(img_path)
        except Exception:
            pass

        # Preserve hint until we get an actual agent turn. Otherwise the very
        # first audio (often a partial echo or VAD false-trigger) consumes the
        # mode hint and the next turn falls back to router triage. "wait" is
        # the server's semantic-endpoint signal that the user trailed off mid
        # sentence; we keep the hint and loop back to listen for the rest.
        active = info.get("active_agent", "")
        if active and active not in ("silence", "barge", "wait"):
            hint = None
        print("[stream_turn done] info={0}".format(info))
        if active == "wait":
            # Brief green pulse so the user sees we're still listening.
            try:
                leds.fadeRGB("FaceLeds", 0.0, 1.0, 0.0, 0.08)
            except Exception:
                pass
            skip_tts_wait = True
            continue
        if info.get("barge_in"):
            print("[barge-in] user interrupted NAO speech; listening now")
            # Visual confirmation: hold yellow for ~400ms so the user actually
            # sees the acknowledgement before record_audio paints the eyes
            # green for listening.
            try:
                leds.fadeRGB("FaceLeds", 1.0, 0.5, 0.0, 0.08)  # 80ms fade-in
                time.sleep(0.4)                                 # hold
            except Exception:
                pass
            skip_tts_wait = True
            continue
        if info.get("crisis"):
            print("[exit reason] crisis flag")
            _mark_session_ended()
            break
        if info.get("suppress_image"):
            suppress_image = True
        user_input = info.get("user_input") or ""
        action = _intent.detect(user_input, current_mode=initial_hint or "")
        if action == "exit":
            print("[exit reason] exit_intent on: {0!r}".format(user_input))
            try:
                requests.post(
                    "http://{0}:{1}/turn".format(config.SERVER_IP, config.SERVER_PORT),
                    data={"username": username, "end_session": "true"},
                    headers=_auth_headers(),
                    timeout=10,
                )
            except Exception:
                pass
            expressive_say(raw_tts, "Goodbye, {0}. See you next time.".format(username))
            _mark_session_ended()
            return None
        if action and action.startswith("switch:"):
            target = action.split(":", 1)[1]
            print("[switch] {0} -> {1}".format(initial_hint, target))
            expressive_say(raw_tts, "Switching to {0} mode.".format(target))
            return target
