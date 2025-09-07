# wake_listener.py
# wake word + quick actions; no forced stand for wave; debounced + ASR paused during TTS

from naoqi import ALProxy
import time
import math

DEBOUNCE_SECONDS = 2.0
COOLDOWN_SAME_WORD = 3.0
MIN_CONF = 0.55
FACE_GREETING_TIMEOUT = 6.0

# ----- tiny helpers -----
def _stiffen(motion, on=True):
    try: motion.setStiffnesses("Body", 1.0 if on else 0.0)
    except: pass

def _safe_stand(posture):
    try:
        if posture.getPostureFamily() != "Standing":
            posture.goToPosture("StandInit", 0.6)
    except: pass

def _safe_sit(posture):
    try: posture.goToPosture("Sit", 0.6)
    except: pass

def _say_quiet(tts, asr, text):
    # pause ASR while we speak; reduces echo re-triggers
    try: asr.pause(True)
    except: pass
    try: tts.say(text)
    except: pass
    time.sleep(0.05)
    try: asr.pause(False)
    except: pass

def _flush_word(memory):
    try: memory.insertData("WordRecognized", ["", 0.0])
    except: pass

# wave in current posture (sitting or standing)
def _wave(motion, posture):
    try:
        fam = posture.getPostureFamily()
    except:
        fam = "Unknown"

    _stiffen(motion, True)
    try:
        if fam == "Sitting":
            names  = ["RShoulderPitch","RShoulderRoll","RElbowYaw","RElbowRoll","RWristYaw"]
            angles = [ 0.0,             0.25,           1.2,         0.9,           0.0 ]
            motion.angleInterpolationWithSpeed(names, angles, 0.35)
            for _ in range(2):
                motion.angleInterpolationWithSpeed("RShoulderRoll", 0.05, 0.35)
                motion.angleInterpolationWithSpeed("RShoulderRoll", 0.30, 0.35)
        else:
            names  = ["RShoulderPitch","RShoulderRoll","RElbowYaw","RElbowRoll","RWristYaw"]
            angles = [-0.2,             0.25,           1.2,         1.0,           0.0 ]
            motion.angleInterpolationWithSpeed(names, angles, 0.4)
            for _ in range(3):
                motion.angleInterpolationWithSpeed("RShoulderRoll", -0.05, 0.4)
                motion.angleInterpolationWithSpeed("RShoulderRoll",  0.30, 0.4)
    except:
        pass

# small safe moves
def _move_forward(motion, d=0.15):
    try: _stiffen(motion, True); motion.moveTo(d, 0.0, 0.0)
    except: pass

def _move_back(motion, d=0.12):
    try: _stiffen(motion, True); motion.moveTo(-d, 0.0, 0.0)
    except: pass

def _turn_left(motion, deg=30):
    try: _stiffen(motion, True); motion.moveTo(0.0, 0.0, math.radians(deg))
    except: pass

def _turn_right(motion, deg=30):
    try: _stiffen(motion, True); motion.moveTo(0.0, 0.0, -math.radians(deg))
    except: pass

def _stop_move(motion):
    try: motion.stopMove()
    except: pass

# greet + one clear instruction
def _greet_intro(nao_ip, port):
    tts     = ALProxy("ALTextToSpeech",   nao_ip, port)
    asr     = ALProxy("ALSpeechRecognition", nao_ip, port)
    motion  = ALProxy("ALMotion",         nao_ip, port)
    posture = ALProxy("ALRobotPosture",   nao_ip, port)
    aware   = ALProxy("ALBasicAwareness", nao_ip, port)
    memory  = ALProxy("ALMemory",         nao_ip, port)

    try:
        aware.setEngagementMode("FullyEngaged")
        aware.startAwareness()
    except: pass

    start = time.time()
    while (time.time() - start) < FACE_GREETING_TIMEOUT:
        try:
            if memory.getData("FaceDetected"): break
        except: pass
        time.sleep(0.1)

    _say_quiet(tts, asr, "Hello! My name is Nao. Nice to meet you.")
    _wave(motion, posture)
    _say_quiet(tts, asr, "To begin, say: let's chat. I will help you pick a mode.")
    _flush_word(memory)

# ----- main listener -----
def listen_for_command(nao_ip, port=9559):
    tts     = ALProxy("ALTextToSpeech",      nao_ip, port)
    asr     = ALProxy("ALSpeechRecognition", nao_ip, port)
    memory  = ALProxy("ALMemory",            nao_ip, port)
    motion  = ALProxy("ALMotion",            nao_ip, port)
    posture = ALProxy("ALRobotPosture",      nao_ip, port)

    vocab = [
        "nao",
        "let's chat","let's talk","talk mode","start a conversation","chat mode",
        "wave","stand up","sit down",
        "come forward","go forward","move forward",
        "go back","back up","move back",
        "turn left","turn right","stop"
    ]

    # clean subscribe
    try: asr.unsubscribe("NAO_Chat_Listener")
    except: pass
    asr.pause(True)
    asr.setLanguage("English")
    asr.setVocabulary(vocab, False)
    asr.pause(False)
    asr.subscribe("NAO_Chat_Listener")

    _say_quiet(tts, asr, "I'm listening. Say 'NAO' to wake me.")
    last_trigger_time = 0.0
    last_word = ""

    while True:
        data = memory.getData("WordRecognized")
        if isinstance(data, list) and len(data) == 2:
            word, conf = data
            word = (word or "").lower()
            now = time.time()

            # basic guards
            if not word or word not in vocab or conf < MIN_CONF:
                time.sleep(0.05)
                continue

            # debounce + same-word cooldown
            if (now - last_trigger_time) < DEBOUNCE_SECONDS:
                time.sleep(0.05)
                continue
            if word == last_word and (now - last_trigger_time) < COOLDOWN_SAME_WORD:
                time.sleep(0.05)
                continue

            # mark early to block echoes
            last_trigger_time = now
            last_word = word

            try: asr.unsubscribe("NAO_Chat_Listener")
            except: pass
            print("[Heard]:", word, "conf=", conf)

            # handle
            if word == "nao":
                _greet_intro(nao_ip, port)

            elif word in ["let's chat","let's talk","talk mode","start a conversation","chat mode"]:
                _say_quiet(tts, asr, "Okay, let’s have a chat!")
                _flush_word(memory)
                return "chat"

            elif word == "wave":
                _wave(motion, posture)

            elif word == "stand up":
                _safe_stand(posture)
                _say_quiet(tts, asr, "Standing.")

            elif word == "sit down":
                _safe_sit(posture)
                _say_quiet(tts, asr, "Sitting.")

            elif word in ["come forward","go forward","move forward"]:
                _move_forward(motion)
                _say_quiet(tts, asr, "Moving forward.")

            elif word in ["go back","back up","move back"]:
                _move_back(motion)
                _say_quiet(tts, asr, "Moving back.")

            elif word == "turn left":
                _turn_left(motion)
                _say_quiet(tts, asr, "Turning left.")

            elif word == "turn right":
                _turn_right(motion)
                _say_quiet(tts, asr, "Turning right.")

            elif word == "stop":
                _stop_move(motion)
                _say_quiet(tts, asr, "Stopped.")

            # resume
            _flush_word(memory)
            try: asr.subscribe("NAO_Chat_Listener")
            except: pass
            time.sleep(0.25)  # small settle

        time.sleep(0.05)
