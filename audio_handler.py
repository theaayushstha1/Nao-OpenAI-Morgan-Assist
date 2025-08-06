# audio_handler.py
# -*- coding: utf-8 -*-
"""
Record and clean a WAV on NAO:
  - Blink LEDs while listening
  - Trim leading/trailing silence
  - Normalize peak volume
  - Return the final WAV path (on the robot)
"""

import os
import time
import wave
import audioop
from naoqi import ALProxy

# ──────────────────────────────────────────────────────────────────────────────
SAVE_DIR     = "/home/nao/recordings"
MAX_DURATION = 4.0         # record window (seconds)
SILENCE_THRES = 500        # RMS threshold for trimming
SILENCE_CHUNK = 1024       # bytes per trim chunk
# ──────────────────────────────────────────────────────────────────────────────

def record_audio(nao_ip, max_duration=MAX_DURATION):
    # 1) Ensure storage dir
    if not os.path.exists(SAVE_DIR):
        os.makedirs(SAVE_DIR)

    # 2) Timestamped filename
    ts   = time.strftime("%Y%m%d_%H%M%S")
    path = "{}/nao_rec_{}.wav".format(SAVE_DIR, ts)

    # 3) LED→green, start recording
    leds     = ALProxy("ALLeds", nao_ip, 9559)
    recorder = ALProxy("ALAudioRecorder", nao_ip, 9559)
    leds.fadeRGB("FaceLeds", 0.0, 1.0, 0.0, 0.1)
    try:
        recorder.stopMicrophonesRecording()
    except:
        pass
    recorder.startMicrophonesRecording(path, "wav", 16000, (0,0,1,0))

    # 4) Wait while listening
    time.sleep(max_duration)
    recorder.stopMicrophonesRecording()
    leds.fadeRGB("FaceLeds", 1.0, 1.0, 1.0, 0.1)

    # 5) Trim silence
    cleaned = _trim_silence(path, SILENCE_THRES, SILENCE_CHUNK) or path

    # 6) Normalize peak
    normalized = _normalize_audio(cleaned) or cleaned
    return normalized


def _trim_silence(wav_path, threshold, chunk_size):
    try:
        wf     = wave.open(wav_path, "rb")
        nchan, width, rate, nframes, comp, name = wf.getparams()
        raw    = wf.readframes(nframes)
        wf.close()

        # find first loud byte
        start = 0
        while start < len(raw):
            if audioop.rms(raw[start:start+chunk_size], width) > threshold:
                break
            start += chunk_size

        # find last loud byte
        end = len(raw)
        while end > start:
            if audioop.rms(raw[end-chunk_size:end], width) > threshold:
                break
            end -= chunk_size

        trimmed = raw[start:end]
        out_path = wav_path.replace(".wav", "_trim.wav")
        wf2 = wave.open(out_path, "wb")
        wf2.setparams((nchan, width, rate, len(trimmed)//(width*nchan), comp, name))
        wf2.writeframes(trimmed)
        wf2.close()
        return out_path

    except Exception as e:
        print("trim_silence error: {}".format(e))
        return None


def _normalize_audio(wav_path):
    try:
        wf = wave.open(wav_path, "rb")
        nchan, width, rate, nframes, comp, name = wf.getparams()
        raw = wf.readframes(nframes)
        wf.close()

        peak = audioop.max(raw, width)
        if peak == 0:
            return None
        max_val = float((2 ** (8*width - 1)) - 1)
        factor  = max_val / peak

        normalized = audioop.mul(raw, width, factor)
        out_path   = wav_path.replace(".wav", "_norm.wav")
        wf2 = wave.open(out_path, "wb")
        wf2.setparams((nchan, width, rate, len(normalized)//(width*nchan), comp, name))
        wf2.writeframes(normalized)
        wf2.close()
        return out_path

    except Exception as e:
        print("normalize_audio error: {}".format(e))
        return None
