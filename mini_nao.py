# -*- coding: utf-8 -*-
from __future__ import print_function
import os, time, json, re, threading, requests, calendar
import datetime as _dt
from naoqi import ALProxy


# ──────────────────────────────────────────────────────────────────────────────
# CONFIG 
# ──────────────────────────────────────────────────────────────────────────────
CHECK_INTERVALS = 0.25  # timer/reminder check frequency
FAST_MODE = True  # fewer follow-up prompts
FAST_REMINDER_DEFAULT_MIN = 10

BALT_LAT, BALT_LON = 39.2904, -76.6122  # Baltimore, MD

SERVER_IP   = "172.20.95.105"
SERVER_URL  = "http://{}:5000/upload".format(SERVER_IP)

STATE_FILE = "/data/home/nao/Sound/state.json"
TIMER_MP3  = "/data/home/nao/Sound/timer.mp3"

SESSION = requests.Session()
DEFAULT_TIMEOUT = 8


# ──────────────────────────────────────────────────────────────────────────────
# STORAGE
# ────────────────────────────────────────────────────────────────────────────── 
_DEFAULT_STATE = {
    "todos": [],
    "reminders": [],
    "timers": [],
    "user": "friend"
}

def _ensure_dirs():
    dirpath = os.path.dirname(STATE_FILE)
    if dirpath and not os.path.exists(dirpath):
        try: os.makedirs(dirpath)
        except: pass

def _load_state():
    _ensure_dirs()
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except: pass
    return dict(_DEFAULT_STATE)

def _save_state(state):
    _ensure_dirs()
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except: pass


# ──────────────────────────────────────────────────────────────────────────────
# UTC → NEW YORK (EST/EDT) 
# ──────────────────────────────────────────────────────────────────────────────
def _second_sunday_of_march(year):
    d = _dt.date(year, 3, 1)
    first_sun = (6 - d.weekday()) % 7
    day = 1 + first_sun
    if day <= 7: day += 7
    return _dt.date(year, 3, day)

def _first_sunday_of_november(year):
    d = _dt.date(year, 11, 1)
    first_sun = (6 - d.weekday()) % 7
    return _dt.date(year, 11, 1 + first_sun)

def _is_us_dst_for_newyork(utc_dt):
    year  = utc_dt.year
    start = _second_sunday_of_march(year)
    end   = _first_sunday_of_november(year)
    utc_date = utc_dt.date()
    if utc_date < start or utc_date > end: return False
    if start < utc_date < end: return True
    if utc_date == start: return utc_dt.hour >= 6
    if utc_date == end:   return utc_dt.hour < 6
    return False

def _utc_now():
    t = _dt.datetime.utcnow()
    return _dt.datetime(t.year, t.month, t.day, t.hour, t.minute, t.second)

def _utc_to_newyork(utc_dt):
    offset_hours = -4 if _is_us_dst_for_newyork(utc_dt) else -5
    return utc_dt + _dt.timedelta(hours=offset_hours)

def _fmt_hhmm_ampm(local_dt):
    h = local_dt.hour % 12 or 12
    ap = "A M" if local_dt.hour < 12 else "P M"
    return "{}:{:02d} {}".format(h, local_dt.minute, ap)


# ──────────────────────────────────────────────────────────────────────────────
# EXIT DETECTION
# ──────────────────────────────────────────────────────────────────────────────
EXIT_PATTERNS = [
    r"\b(exit|quit|stop|end|goodbye|bye|close)\b.*\b(chat|mode|conversation|talking|session)\b",
    r"\b(chat|mode|conversation|talking|session)\b.*\b(exit|quit|stop|end|goodbye|bye|close)\b",
    r"^(exit|quit|stop now|end chat|goodbye|bye bye|that's all|that is all)$",
    r"^(i'm done|i am done|we're done|we are done)$",
    r"^(stop talking|stop listening|no more)$",
    r"\b(i (want|need) to (go|leave|stop)|let me (go|leave)|gotta go)\b",
    r"\b(talk to you later|catch you later|see you later)\b",
    r"\b(thanks.*bye|thank you.*bye|thanks.*good(bye)?)\b",
    r"\b(stop.*mode|exit.*mode|leave.*mode|quit.*mode)\b",
    r"\b(go back|return|switch back)\b.*\b(wake|main|menu)\b",
]

EXIT_KEYWORDS = [
    "exit", "quit", "stop", "end", "goodbye", "bye", "close",
    "done", "finished", "that's all", "no more", "leave", "go back"
]

def _is_exit_intent(text):
    """Check transcribed text for exit intent"""
    t = (text or "").strip().lower()
    if not t: return False
    
    # Don't exit for "stop the timer/music..."
    for tw in ("timer","alarm","music","sound","ringtone","countdown"):
        if ("stop " + tw) in t or ("stop the " + tw) in t:
            return False
    
    # Check regex patterns
    for pattern in EXIT_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            print("[EXIT DETECTED] Pattern: {}".format(pattern))
            return True
    
    # Check keywords in short utterances
    words = t.split()
    if len(words) <= 3:
        for keyword in EXIT_KEYWORDS:
            if keyword in words:
                print("[EXIT DETECTED] Keyword: {}".format(keyword))
                return True
    
    return False


# ──────────────────────────────────────────────────────────────────────────────
# BASIC HELPERS
# ──────────────────────────────────────────────────────────────────────────────
def _say(tts, text):
    try: tts.say(text)
    except: pass

def _hear_text(nao_ip, prompt=None):
    if prompt and not FAST_MODE:
        try: ALProxy("ALTextToSpeech", nao_ip, 9559).say(prompt)
        except: pass
    try:
        from audio_handler import record_audio
        wav = record_audio(nao_ip)
        with open(wav, "rb") as f:
            r = SESSION.post(SERVER_URL, files={"file": f}, data={"username": "MiniNao"}, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        return (r.json() or {}).get("user_input", "") or ""
    except Exception:
        return ""


# ──────────────────────────────────────────────────────────────────────────────
# PARSING 
# ──────────────────────────────────────────────────────────────────────────────
def _parse_duration_seconds(text):
    t = (text or "").lower()
    total = 0

    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes|s|sec|secs|second|seconds)", t, re.I):
        n = float(num); u = unit.lower()
        if u in ("h","hr","hrs","hour","hours"): total += n * 3600
        elif u in ("m","min","mins","minute","minutes"): total += n * 60
        elif u in ("s","sec","secs","second","seconds"): total += n

    if total == 0:
        m = re.search(r"(\d+)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)", t, re.I)
        if m:
            return _parse_duration_seconds(m.group(0))

    return int(total)

def _nearest_future_at_hour_min(local_now, h, mm):
    candidates = []
    today = _dt.datetime(local_now.year, local_now.month, local_now.day, h, mm, 0)
    if today > local_now: candidates.append(today)
    alt = today + _dt.timedelta(hours=12)
    if alt > local_now: candidates.append(alt)
    if not candidates:
        candidates = [today + _dt.timedelta(days=1)]
    return min(candidates)

def _parse_when_epoch(text):
    now = time.time()
    t = (text or "").strip().lower()

    # "in 10m / in 2 hours"
    m = re.search(r"\bin\s+(.+)$", t, re.I)
    if m:
        dur = _parse_duration_seconds(m.group(1))
        if dur > 0: return now + dur

    # "at 7", "at 7:15", "at 7 pm"
    m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", t, re.I)
    if m:
        h = int(m.group(1)); mm = int(m.group(2) or 0); ap = (m.group(3) or "").lower()
        utc_now = _utc_now()
        local_now = _utc_to_newyork(utc_now)
        if ap:
            if ap == "pm" and h < 12: h += 12
            if ap == "am" and h == 12: h = 0
            target_local = _dt.datetime(local_now.year, local_now.month, local_now.day, h, mm, 0)
            if target_local <= local_now: target_local += _dt.timedelta(days=1)
        else:
            target_local = _nearest_future_at_hour_min(local_now, h % 24, mm)

        offset = _utc_to_newyork(_utc_now()) - _utc_now()
        back_utc_guess = target_local - offset
        return calendar.timegm(back_utc_guess.timetuple())

    return None


# ──────────────────────────────────────────────────────────────────────────────
# SKILLS
# ──────────────────────────────────────────────────────────────────────────────
def skill_time(tts):
    ny = _utc_to_newyork(_utc_now())
    _say(tts, "It's " + _fmt_hhmm_ampm(ny))

def skill_date(tts):
    ny = _utc_to_newyork(_utc_now())
    _say(tts, "Today is {} {}, {}".format(ny.strftime("%B"), ny.day, ny.year))

def _wmocode_to_text(code):
    if code in (0,): return "clear skies"
    if code in (1,2,3): return "some clouds"
    if code in (45,48): return "fog"
    if code in (51,53,55,56,57): return "drizzle"
    if code in (61,63,65,66,67): return "rain"
    if code in (71,73,75,77): return "snow"
    if code in (80,81,82): return "showers"
    if code in (95,96,99): return "thunderstorms"
    return "unknown conditions"

def skill_weather_baltimore(tts):
    try:
        r = SESSION.get("https://api.open-meteo.com/v1/forecast",
                        params={"latitude": BALT_LAT, "longitude": BALT_LON,
                                "current_weather": True, "temperature_unit": "fahrenheit"},
                        timeout=8)
        r.raise_for_status()
        cw = (r.json() or {}).get("current_weather") or {}
        temp = cw.get("temperature"); wind = cw.get("windspeed"); code = cw.get("weathercode")
        desc = _wmocode_to_text(code)
        if temp is not None:
            _say(tts, "Baltimore weather: about {} degrees Fahrenheit with {} and wind around {} miles per hour."
                 .format(int(round(temp)), desc, int(round(wind or 0))))
        else:
            _say(tts, "I couldn't fetch the Baltimore weather right now.")
    except Exception:
        _say(tts, "Weather service is not responding.")

def skill_add_todo(tts, state, text, nao_ip):
    m = re.search(r"(?:add|create|new)\s+(?:todo|to[- ]?do|task)\s*(?:called|named|for)?\s*(.*)$", text, re.I)
    item = (m.group(1).strip() if m else "").strip()
    if not item and not FAST_MODE:
        _say(tts, "What should I add to your to do list?")
        item = _hear_text(nao_ip)
    if not item:
        _say(tts, "Okay, canceled."); return
    state["todos"].append({"text": item, "done": False, "ts": int(time.time())})
    _save_state(state)
    _say(tts, "Added: " + item)

def skill_list_todos(tts, state):
    todos = [t for t in state["todos"] if not t.get("done")]
    if not todos:
        _say(tts, "Your to do list is empty."); return
    _say(tts, "You have {} thing{}.".format(len(todos), "" if len(todos)==1 else "s"))
    for i, t in enumerate(todos, 1):
        _say(tts, "{}. {}".format(i, t["text"]))

def skill_complete_todo(tts, state, text):
    m = re.search(r"(?:complete|done|finish|check)\s+(?:number\s+)?(\d+)", text, re.I)
    if m:
        idx = int(m.group(1)) - 1
        pending_idx = [i for i, t in enumerate(state["todos"]) if not t.get("done")]
        if 0 <= idx < len(pending_idx):
            real = pending_idx[idx]; state["todos"][real]["done"] = True; _save_state(state)
            _say(tts, "Marked as done."); return
    for i, t in enumerate(state["todos"]):
        if not t.get("done") and re.search(re.escape(t["text"]), text, re.I):
            state["todos"][i]["done"] = True; _save_state(state); _say(tts, "Marked as done."); return
    _say(tts, "I couldn't find that item.")

def skill_set_timer(tts, state, text, nao_ip):
    seconds = _parse_duration_seconds(text)
    if seconds <= 0 and not FAST_MODE:
        _say(tts, "For how long should I set the timer?")
        spoken = _hear_text(nao_ip); seconds = _parse_duration_seconds(spoken)
    if seconds <= 0:
        _say(tts, "Okay, canceled."); return
    state["timers"].append({"label": "timer", "end": time.time() + seconds, "said": False})
    _save_state(state)
    if seconds >= 60:
        mins = int(round(seconds/60.0)); _say(tts, "Timer set for {} minutes.".format(mins))
    else:
        _say(tts, "Timer set for {} seconds.".format(seconds))

def skill_set_reminder(tts, state, text, nao_ip):
    what = None
    m = re.search(r"remind me (?:to|that)\s+(.+)", text, re.I)
    if m: what = m.group(1).strip()
    when = _parse_when_epoch(text)

    if not when and FAST_MODE:
        when = time.time() + FAST_REMINDER_DEFAULT_MIN * 60

    if not when and not FAST_MODE:
        _say(tts, "When should I remind you?")
        spoken = _hear_text(nao_ip); when = _parse_when_epoch(spoken)

    if not what and not FAST_MODE:
        _say(tts, "What should I remind you about?")
        what = _hear_text(nao_ip).strip()

    if not what:
        _say(tts, "Okay, canceled."); return

    state["reminders"].append({"text": what, "epoch": float(when), "said": False})
    _save_state(state)
    ny = _utc_to_newyork(_dt.datetime.utcfromtimestamp(when))
    _say(tts, "Reminder saved for {} about {}.".format(_fmt_hhmm_ampm(ny), what))

def skill_list_reminders(tts, state):
    future = [r for r in state["reminders"] if (not r.get("said")) and r["epoch"] > time.time()]
    if not future:
        _say(tts, "You have no upcoming reminders."); return
    _say(tts, "You have {} upcoming reminder{}.".format(len(future), "" if len(future)==1 else "s"))
    future.sort(key=lambda x: x["epoch"])
    for r in future[:5]:
        ny = _utc_to_newyork(_dt.datetime.utcfromtimestamp(r["epoch"]))
        _say(tts, "{} — {}".format(_fmt_hhmm_ampm(ny), r["text"]))


# ──────────────────────────────────────────────────────────────────────────────
# NOTIFIER (timers/reminders) + MP3 alarm
# ──────────────────────────────────────────────────────────────────────────────
def _play_timer_sound(nao_ip):
    try:
        if os.path.exists(TIMER_MP3):
            player = ALProxy("ALAudioPlayer", nao_ip, 9559)
            player.playFile(TIMER_MP3)
        else:
            ALProxy("ALTextToSpeech", nao_ip, 9559).say("Timer finished.")
    except:
        try: ALProxy("ALTextToSpeech", nao_ip, 9559).say("Timer finished.")
        except: pass

def _notifier_loop(nao_ip, stop_flag):
    tts = ALProxy("ALTextToSpeech", nao_ip, 9559)
    while not stop_flag["stop"]:
        state = _load_state()
        now = time.time()
        changed = False

        for t in state["timers"]:
            if not t.get("said") and now >= t["end"]:
                _say(tts, "Time's up.")
                _play_timer_sound(nao_ip)
                t["said"] = True; changed = True

        for r in state["reminders"]:
            if not r.get("said") and now >= r["epoch"]:
                _say(tts, "Reminder: {}.".format(r.get("text","")))
                r["said"] = True; changed = True

        if changed: _save_state(state)
        time.sleep(CHECK_INTERVALS)


# ──────────────────────────────────────────────────────────────────────────────
# ROUTER
# ──────────────────────────────────────────────────────────────────────────────
def handle_command(nao_ip, text):
    text = (text or "").strip()
    tts  = ALProxy("ALTextToSpeech", nao_ip, 9559)
    low = text.lower()

    if re.search(r"\b(time|what.*time is it)\b", low):
        skill_time(tts); return
    if re.search(r"\b(date|what.*date|what.*day)\b", low):
        skill_date(tts); return
    if "weather" in low:
        skill_weather_baltimore(tts); return

    state = _load_state()

    if re.search(r"\bset (?:a )?timer\b", low) or re.search(r"\bstart (?:a )?timer\b", low):
        skill_set_timer(tts, state, text, nao_ip); return
    if re.search(r"\bremind me\b", low) or "set reminder" in low:
        skill_set_reminder(tts, state, text, nao_ip); return
    if "list reminders" in low or "upcoming reminders" in low:
        skill_list_reminders(tts, state); return
    if re.search(r"\badd (?:todo|to[- ]?do|task)\b", low) or "new todo" in low:
        skill_add_todo(tts, state, text, nao_ip); return
    if "list to do" in low or "list todo" in low or "show my to do" in low:
        skill_list_todos(tts, state); return
    if "complete" in low or "mark as done" in low or "check off" in low:
        skill_complete_todo(tts, state, text); return

    _say(tts, "MiniNao can tell time, weather for Baltimore, set timers with a ringtone, reminders, and to do lists.")


# ──────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────
def enter_mini_nao_mode(nao_ip="127.0.0.1", port=9559):
    tts = ALProxy("ALTextToSpeech", nao_ip, port)
    posture = ALProxy("ALRobotPosture", nao_ip, port)
    try: posture.goToPosture("StandInit", 0.6)
    except: pass

    _ensure_dirs()
    _say(tts, "MiniNao is ready.")

    stop_flag = {"stop": False}
    th = threading.Thread(target=_notifier_loop, args=(nao_ip, stop_flag))
    th.daemon = True; th.start()

    from audio_handler import record_audio
    try:
        while True:
            wav = record_audio(nao_ip)
            try:
                with open(wav, "rb") as f:
                    r = SESSION.post(SERVER_URL, files={"file": f}, data={"username": "MiniNao"}, timeout=DEFAULT_TIMEOUT)
                r.raise_for_status()
                user_text = (r.json() or {}).get("user_input","")
            except Exception:
                continue

            if not user_text:
                continue

            print("[USER INPUT] {}".format(user_text))

            # Check server's transcription for exit intent
            if _is_exit_intent(user_text):
                _say(tts, "Exiting MiniNao.")
                print("👋 Exit detected")
                break

            handle_command(nao_ip, user_text)
    finally:
        stop_flag["stop"] = True
