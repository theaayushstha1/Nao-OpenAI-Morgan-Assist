# -*- coding: utf-8 -*-
from __future__ import print_function
import os, json, random, requests, time, re, threading
from naoqi import ALProxy
from utils.camera_capture import capture_photo
from processing_announcer import ProcessingAnnouncer
import memory_manager

SERVER_IP = os.environ.get("SERVER_IP", "172.20.95.120")
SERVER_URL = "http://{}:5000/upload".format(SERVER_IP)
CHAT_TEXT_URL = "http://{}:5000/chat_text".format(SERVER_IP)
FACE_RECO_URL = "http://{}:5000/face/recognize".format(SERVER_IP)
FACE_ENROLL_URL = "http://{}:5000/face/enroll".format(SERVER_IP)
SESSION = requests.Session()
DEFAULT_TIMEOUT = 30

VOICE_PROFILES = {
    "general": {"speed": 100, "pitch": 0.95},
    "study": {"speed": 110, "pitch": 1.19},
    "therapist": {"speed": 85, "pitch": 0.85},
    "broker": {"speed": 95, "pitch": 1.10},
}
VALID_FOR_SERVER = ("general", "study", "therapist", "broker")
def _canon_for_server(m): return m if m in VALID_FOR_SERVER else "general"

def _apply_mode_voice(tts, mode):
    p = VOICE_PROFILES.get(mode, VOICE_PROFILES["general"])
    try:
        tts.setParameter("speed", float(p["speed"]))
        tts.setParameter("pitchShift", float(p["pitch"]))
        tts.setVolume(1.0)
    except: pass

def _reset_voice(tts): _apply_mode_voice(tts, "general")
def _stop_tts(tts):
    try:
        stop_all = getattr(tts, "stopAll", None)
        if callable(stop_all): stop_all()
    except: pass

def call_with_processing_announcer(tts, func):
    ann = ProcessingAnnouncer(tts_say=lambda s:_say(tts,s),
                              stop_all=getattr(tts,"stopAll",None),
                              first_delay=2.5, interval=3.5, max_utterances=2)
    ann.start()
    try: return func()
    finally:
        try: ann.stop(interrupt=True)
        finally: _stop_tts(tts)

try: unicode_type = unicode
except NameError: unicode_type = str

def _to_sayable(t):
    try:
        if t is None: s=u"Okay."
        elif isinstance(t,str):
            try: s=t.decode('utf-8','ignore')
            except: s=unicode_type(t)
        elif isinstance(t,unicode_type): s=t
        else: s=unicode_type(t)
        s=u''.join(c if 32<=ord(c)<=126 else u' ' for c in s).strip()
        return s.encode('utf-8') if s else "Okay."
    except: return "Okay."

def _say(robot,text):
    try: robot.say(_to_sayable(text))
    except Exception as e: print("[WARN] say:",e)

KEYWORDS = {
    "general":["general","normal","default"],
    "study":["study","school","homework","learn"],
    "therapist":["therapist","therapy","mental","stress","mood"],
    "broker":["broker","stock","market","finance"]
}
def _extract_mode_from_text(t):
    if not t: return None
    t=t.lower()
    for m,kws in KEYWORDS.items():
        if any(re.search(r"\b"+re.escape(k)+r"\b",t) for k in kws): return m
    return None

def _color_to_rgb(n):
    return {"red":[1,0,0],"green":[0,1,0],"blue":[0,0,1],
            "yellow":[1,1,0],"purple":[1,0,1],"white":[1,1,1]}.get((n or "").lower(),[1,1,1])

def extract_name(t):
    m=re.search(r"(?:my name is|call me|i am)\s+([A-Za-z]+)",(t or "").lower())
    return m.group(1).capitalize() if m else "friend"

def _post_image(url,img_path,extra=None,timeout=6.0):
    with open(img_path,"rb") as f:
        files={"file":(os.path.basename(img_path),f,"image/jpeg")}
        r=SESSION.post(url,files=files,data=(extra or {}),timeout=timeout)
        r.raise_for_status(); return r.json()

def get_available_gestures(behav_mgr):
    try: allb=behav_mgr.getInstalledBehaviors()
    except: return []
    built=[b for b in allb if "animations/Stand/Gestures/" in b]
    pri=[g for g in built if any(k in g for k in ["Explain","ShowSky","YouKnowWhat","Point","Yes","No","ComeOn","This"])]
    if len(pri)<10: pri+=random.sample(built,min(len(built),10-len(pri)))
    return sorted(list(set(pri)))

def _split_sentences(t):
    return [p.strip() for p in re.split(r'(?<=[.!?]) +',t) if p.strip()]

def _loop_gestures(behav_mgr,pool,stop):
    last=None
    while not stop.is_set() and pool:
        try:
            g=random.choice([x for x in pool if x!=last] or pool)
            last=g; print("[Gesture]",g)
            if behav_mgr.isBehaviorRunning(g): behav_mgr.stopBehavior(g)
            behav_mgr.runBehavior(g)
            time.sleep(random.uniform(1.0,1.8))
        except Exception as e:
            print("[G err]",e); time.sleep(1)

def _speak_with_gestures(robot,tts,behav_mgr,text,mode,pool):
    parts=_split_sentences(text) or [text]
    for p in parts:
        stop=threading.Event()
        th=threading.Thread(target=_loop_gestures,args=(behav_mgr,pool,stop))
        th.daemon=True; th.start()
        time.sleep(0.05)
        _say(robot,p)
        stop.set(); th.join(timeout=0.1)
        try: behav_mgr.stopAllBehaviors()
        except: pass
        time.sleep(0.1)

# --- Face recognition ---
def recognize_or_enroll(robot,nao_ip,port):
    from audio_handler import record_audio
    photo_path=capture_photo(nao_ip,port,"/home/nao/face.jpg")

    # Try recognizing
    if photo_path and os.path.exists(photo_path):
        try:
            info=_post_image(FACE_RECO_URL,photo_path,{"tolerance":"0.60"})
            if info.get("ok") and info.get("match"):
                name=info.get("name") or "friend"
                _say(robot,"Welcome back, {}! I recognize you.".format(name))
                return name,True
        except: pass

    # New user
    _say(robot,"I don't know you yet. Please tell me your first name.")
    time.sleep(0.3)
    wav=record_audio(nao_ip)
    user="friend"
    try:
        with open(wav,"rb") as f:
            r=SESSION.post(SERVER_URL,files={"file":f},data={"username":user},timeout=DEFAULT_TIMEOUT)
        spoken=(r.json() or {}).get("user_input",""); e=extract_name(spoken)
        if e and e.lower()!="friend": user=e
    except: pass

    if user=="friend":
        _say(robot,"I'll call you friend for now."); return user,False

    _say(robot,"Nice to meet you, {}. Let me take your picture.".format(user))
    for _ in range(3):
        time.sleep(0.4)
        p=capture_photo(nao_ip,port,"/home/nao/face.jpg")
        if p and os.path.exists(p):
            try:_post_image(FACE_ENROLL_URL,p,{"name":user})
            except: pass
    _say(robot,"All set, {}! I'll remember you next time.".format(user))
    return user,False

def _pick_mode(robot,nao_ip,user,default="general"):
    from audio_handler import record_audio
    _say(robot,"Choose a chat mode.")
    def hear():
        w=record_audio(nao_ip)
        try:
            with open(w,"rb") as f:
                r=SESSION.post(SERVER_URL,files={"file":f},data={"username":user},timeout=DEFAULT_TIMEOUT)
            r.raise_for_status(); d=r.json() or {}
            sm=(d.get("active_mode") or "").lower()
            if sm in VALID_FOR_SERVER: return sm
            return _extract_mode_from_text(d.get("user_input",""))
        except: return None
    m=hear() or hear()
    if not m: _say(robot,"Using {} mode.".format(default)); return default
    _say(robot,"{} mode selected.".format(m.capitalize())); return m

def _requery_immediate(user,text,new_mode):
    try:
        p={"username":user,"text":text,"mode":_canon_for_server(new_mode)}
        r=SESSION.post(CHAT_TEXT_URL,json=p,timeout=DEFAULT_TIMEOUT)
        r.raise_for_status(); return r.json()
    except: return None

def _mode_enter_actions(robot,posture,tts,behav_mgr,mode):
    if mode=="therapist":
        _say(robot,"Please sit with me.")
        try: posture.goToPosture("Sit",0.6)
        except: pass
    elif mode=="study":
        _say(robot,"Stand with me, let's learn together.")
        try: posture.goToPosture("StandInit",0.6)
        except: pass

# --- Main loop ---
def enter_chat_mode(robot,nao_ip="127.0.0.1",port=9559):
    motion=ALProxy("ALMotion",nao_ip,port)
    posture=ALProxy("ALRobotPosture",nao_ip,port)
    leds=ALProxy("ALLeds",nao_ip,port)
    tts=ALProxy("ALTextToSpeech",nao_ip,port)
    behav_mgr=ALProxy("ALBehaviorManager",nao_ip,port)

    pool=get_available_gestures(behav_mgr)
    _reset_voice(tts)
    _say(robot,"Scanning for a friend...")

    try:
        from utils.face_utils import detect_face,detect_mood
        if not detect_face(nao_ip):
            _say(robot,"I don't see anyone yet."); return
        mood=detect_mood(nao_ip) or "neutral"
    except: mood="neutral"

    r,g,b=_color_to_rgb({"happy":"yellow","neutral":"white","annoyed":"purple"}.get(mood,"white"))
    try: leds.fadeRGB("FaceLeds",r,g,b,0.3)
    except: pass

    name,known=recognize_or_enroll(robot,nao_ip,port)
    if known: _say(robot,"Welcome back, {}!".format(name))

    mode=_pick_mode(robot,nao_ip,name)
    _apply_mode_voice(tts,mode)
    _mode_enter_actions(robot,posture,tts,behav_mgr,mode)
    _say(robot,"Hey {}! {} mode is on.".format(name,mode.capitalize()))

    try: memory_manager.initialize_user(name)
    except: pass

    from audio_handler import record_audio
    while True:
        _say(robot,"I’m listening.")
        path=record_audio(nao_ip)
        if not os.path.exists(path):
            _say(robot,"Repeat please."); continue
        def call():
            with open(path,"rb") as f:
                return SESSION.post(SERVER_URL,files={"file":f},
                    data={"username":name,"mode":_canon_for_server(mode)},timeout=DEFAULT_TIMEOUT)
        try:
            res=call_with_processing_announcer(tts,call); res.raise_for_status()
            data=res.json()
        except:
            _say(robot,"Connection hiccup."); continue

        user_t=data.get("user_input","") or ""
        reply=data.get("reply","") or ""
        func=data.get("function_call",{}) or {}
        server_m=(data.get("active_mode") or "").lower() or None
        switch=False

        if data.get("mode_prompt"):
            _say(robot,"Which mode do you want?")
            pick=_pick_mode(robot,nao_ip,name,default=(server_m or mode))
            if pick and pick!=mode:
                mode=pick; _apply_mode_voice(tts,mode)
                _mode_enter_actions(robot,posture,tts,behav_mgr,mode)
                again=_requery_immediate(name,user_t,mode)
                reply=(again or {}).get("reply","") or "✅ Switched to {} mode.".format(mode)
                switch=True
        elif server_m and server_m!=mode:
            mode=server_m; _apply_mode_voice(tts,mode)
            _mode_enter_actions(robot,posture,tts,behav_mgr,mode)
            reply="✅ Switched to {} mode.".format(mode); switch=True

        try:
            if user_t: memory_manager.add_user_message(name,user_t)
            memory_manager.add_bot_reply(name,reply if reply else json.dumps(func))
            memory_manager.save_chat_history(name)
        except: pass

        if reply: _speak_with_gestures(robot,tts,behav_mgr,reply,mode,pool)
        elif switch: _speak_with_gestures(robot,tts,behav_mgr,"✅ Switched to {} mode.".format(mode),mode,pool)
        if "stop" in user_t.lower():
            _say(robot,"Catch you later!"); break

        f=func.get("name")
        if f=="stand_up":
            try: posture.goToPosture("StandInit",0.6)
            except: pass
        elif f=="sit_down":
            try: posture.goToPosture("Sit",0.6)
            except: pass
        elif f=="down":
            try:
                motion.setStiffnesses("Body",1.0)
                j=["RHipPitch","LHipPitch","RKneePitch","LKneePitch","RAnklePitch","LAnklePitch"]
                a=[0.3,0.3,0.5,0.5,-0.2,-0.2]; motion.setAngles(j,a,0.2)
            except: pass

    _reset_voice(tts)
