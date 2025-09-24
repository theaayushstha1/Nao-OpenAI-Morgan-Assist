# audio_handler.py
# -*- coding: utf-8 -*-
"""
Front-mic capture with long-silence VAD stop, trimming, pre-emphasis, AGC.
Returns final WAV path.
"""
from __future__ import print_function
import os, time, wave, audioop, struct
from naoqi import ALProxy

# Paths / format
SAVE_DIR        = "/home/nao/recordings"
SAMPLE_RATE     = 16000
CHANNELS_MASK   = (0, 0, 1, 0)   # front mic mono
SAMPLE_WIDTH    = 2              # S16_LE

# Timing
CALIBRATION_MS      = 240
POLL_MS             = 30
NO_SPEECH_TIMEOUT_S = 10.0       # more time to begin speaking
MIN_CLIP_SEC        = 0.25

# Stop behavior (single long-silence gate)
TRAIL_MS            = 600      # stop after 0.6s of silence

# Durations
DEFAULT_MAX_SEC     = 300.0      # allow up to 5 min per turn
ABS_HARD_CAP_SEC    = 600.0

# Energy thresholds
ENERGY_MIN_START    = 1200
ENERGY_MIN_KEEP     = 800
START_BONUS         = 800
KEEP_MARGIN         = 0.60

# Trimming
TRIM_FRACTION       = 0.40
TRIM_CHUNK_BYTES    = 1024

# Post-FX
PREEMPH_ENABLED     = True
PREEMPH_COEFF       = 0.97
AGC_ENABLED         = True
AGC_TARGET_RMS      = 4500
AGC_MAX_GAIN        = 6.0

# ── Utils ─────────────────────────────────────────────────────────────────────
def _ensure_dir(p):
    if not os.path.exists(p):
        try: os.makedirs(p)
        except: pass

def _ts_path():
    return os.path.join(SAVE_DIR, "nao_rec_{}.wav".format(time.strftime("%Y%m%d_%H%M%S")))

def _fade_leds(ip, r, g, b, t=0.08):
    try: ALProxy("ALLeds", ip, 9559).fadeRGB("FaceLeds", float(r), float(g), float(b), float(t))
    except: pass

def _robot_noise_quiet(ip):
    almoves = None
    try:
        almoves = ALProxy("ALAutonomousMoves", ip, 9559)
        try: almoves.setExpressiveListeningEnabled(False)
        except: pass
        try: almoves.setBackgroundStrategy("none")
        except: pass
    except: pass
    return almoves

def _robot_noise_restore(almoves):
    if almoves:
        try: almoves.setExpressiveListeningEnabled(True)
        except: pass
        try: almoves.setBackgroundStrategy("backToNeutral")
        except: pass

def _calibrate_energy(ip):
    try: audio_dev = ALProxy("ALAudioDevice", ip, 9559)
    except: return ENERGY_MIN_START, ENERGY_MIN_KEEP
    vals = []
    t0 = time.time()
    while (time.time() - t0)*1000.0 < CALIBRATION_MS:
        try: vals.append(float(audio_dev.getFrontMicEnergy()))
        except: vals.append(0.0)
        time.sleep(POLL_MS/1000.0)
    if not vals:
        return ENERGY_MIN_START, ENERGY_MIN_KEEP
    vals.sort()
    base = vals[len(vals)//2]
    start_th = max(ENERGY_MIN_START, base + START_BONUS)
    keep_th  = max(ENERGY_MIN_KEEP, start_th * KEEP_MARGIN)
    return start_th, keep_th

# ── Core ──────────────────────────────────────────────────────────────────────
def record_audio(nao_ip, max_duration=None):
    """
    Record with long-silence stop. Returns final WAV path.
    """
    if max_duration is None:
        max_duration = DEFAULT_MAX_SEC
    max_duration = float(max(1.0, min(max_duration, ABS_HARD_CAP_SEC)))

    _ensure_dir(SAVE_DIR)
    out_path = _ts_path()

    rec = ALProxy("ALAudioRecorder", nao_ip, 9559)
    almoves = _robot_noise_quiet(nao_ip)

    _fade_leds(nao_ip, 0.0, 1.0, 0.0)  # listening

    try: rec.stopMicrophonesRecording()
    except: pass

    rec.startMicrophonesRecording(out_path, "wav", SAMPLE_RATE, CHANNELS_MASK)

    try:
        audio_dev = ALProxy("ALAudioDevice", nao_ip, 9559)
    except:
        audio_dev = None

    start_th, keep_th = _calibrate_energy(nao_ip)
    trim_rms = max(400, int(start_th * TRIM_FRACTION))

    t0 = time.time()
    heard = False
    last_voice_t = None

    time.sleep(0.05)

    try:
        # wait for onset
        while True:
            now = time.time()
            if (now - t0) >= max_duration:
                break
            if (now - t0) >= NO_SPEECH_TIMEOUT_S and not heard:
                break
            e = 0.0
            if audio_dev is not None:
                try: e = float(audio_dev.getFrontMicEnergy())
                except: e = 0.0
            if e >= start_th:
                heard = True
                last_voice_t = now
                break
            time.sleep(POLL_MS/1000.0)

        # track until long trailing silence
        while heard:
            now = time.time()
            if (now - t0) >= max_duration:
                break
            e = 0.0
            if audio_dev is not None:
                try: e = float(audio_dev.getFrontMicEnergy())
                except: e = 0.0
            if e >= keep_th:
                last_voice_t = now
            else:
                if last_voice_t and (now - last_voice_t)*1000.0 >= TRAIL_MS:
                    break
            time.sleep(POLL_MS/1000.0)

    finally:
        try: rec.stopMicrophonesRecording()
        except: pass
        _fade_leds(nao_ip, 1.0, 1.0, 1.0)  # done
        _robot_noise_restore(almoves)

    dur = time.time() - t0
    if dur < MIN_CLIP_SEC:
        time.sleep(max(0.0, MIN_CLIP_SEC - dur))

    trimmed = _trim_silence(out_path, trim_rms, TRIM_CHUNK_BYTES) or out_path
    if PREEMPH_ENABLED:
        pre = _pre_emphasis(trimmed, PREEMPH_COEFF) or trimmed
    else:
        pre = trimmed
    if AGC_ENABLED:
        agc = _agc_to_target_rms(pre, AGC_TARGET_RMS, AGC_MAX_GAIN) or pre
    else:
        agc = pre
    return agc

# ── Post-processing ───────────────────────────────────────────────────────────
def _trim_silence(wav_path, rms_th, chunk_bytes):
    try:
        wf = wave.open(wav_path, "rb")
        nchan, width, rate, nframes, comp, name = wf.getparams()
        raw = wf.readframes(nframes); wf.close()
        if nchan != 1 or width != SAMPLE_WIDTH or not raw:
            return None
        step = max(SAMPLE_WIDTH, int(chunk_bytes / SAMPLE_WIDTH) * SAMPLE_WIDTH)
        start = 0; end = len(raw)
        while start + step <= end and audioop.rms(raw[start:start+step], width) <= rms_th:
            start += step
        while end - step >= start and audioop.rms(raw[end-step:end], width) <= rms_th:
            end -= step
        if end <= start:
            return None
        out = raw[start:end]
        out_path = wav_path.replace(".wav", "_trim.wav")
        wf2 = wave.open(out_path, "wb")
        wf2.setparams((1, width, rate, len(out)//(width*1), comp, name))
        wf2.writeframes(out); wf2.close()
        return out_path
    except Exception as e:
        print("trim_silence error:", e)
        return None

def _pre_emphasis(wav_path, a):
    try:
        wf = wave.open(wav_path, "rb")
        nchan, width, rate, nframes, comp, name = wf.getparams()
        raw = wf.readframes(nframes); wf.close()
        if nchan != 1 or width != SAMPLE_WIDTH or not raw or nframes <= 0:
            return None
        samples = struct.unpack("<{}h".format(nframes), raw)
        out = []
        prev = 0
        for x in samples:
            y = int(x - a * prev)
            if y > 32767: y = 32767
            if y < -32768: y = -32768
            out.append(y); prev = x
        out_bytes = struct.pack("<{}h".format(len(out)), *out)
        out_path = wav_path.replace(".wav", "_pre.wav")
        wf2 = wave.open(out_path, "wb")
        wf2.setparams((1, width, rate, len(out), comp, name))
        wf2.writeframes(out_bytes); wf2.close()
        return out_path
    except Exception:
        return None

def _agc_to_target_rms(wav_path, target_rms, max_gain):
    try:
        wf = wave.open(wav_path, "rb")
        nchan, width, rate, nframes, comp, name = wf.getparams()
        raw = wf.readframes(nframes); wf.close()
        if nchan != 1 or width != SAMPLE_WIDTH or not raw:
            return None
        current = audioop.rms(raw, width)
        if current <= 0:
            return None
        gain = min(max_gain, float(target_rms) / float(current))
        out = audioop.mul(raw, width, gain)
        peak = audioop.max(out, width)
        if peak > 32767:
            out = audioop.mul(out, width, 32767.0/peak)
        out_path = wav_path.replace(".wav", "_agc.wav")
        wf2 = wave.open(out_path, "wb")
        wf2.setparams((1, width, rate, len(out)//(width*1), comp, name))
        wf2.writeframes(out); wf2.close()
        return out_path
    except Exception as e:
        print("agc error:", e)
        return None
