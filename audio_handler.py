# audio_handler.py
# -*- coding: utf-8 -*-
"""
Fast voice capture on NAO:
  - Start recording instantly
  - Stop when we've seen ~700 ms of silence after speech began
  - Hard cap (3.5 s) so it never drags
  - Blink LEDs while listening
  - Trim leading/trailing silence, normalize peak
  - Returns final WAV path (on the robot)
"""

from __future__ import print_function
import os, time, wave, audioop
from naoqi import ALProxy

# Storage & timing
SAVE_DIR        = "/home/nao/recordings"
HARD_CAP_SEC    = 3.5       # absolute maximum record length
MIN_CAP_SEC     = 0.50      # don't stop too early
SILENCE_MS_STOP = 800       # how long of silence to stop after speech started
POLL_MS         = 50        # poll interval for energy (ms)

# Energy thresholds (tune per robot / environment)
ENERGY_START = 3000         # speaking if energy above this
ENERGY_KEEP  = 1800         # considered silence if below this

# Trim parameters
TRIM_RMS_THRES = 500
TRIM_CHUNK     = 1024


def _ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)


def _timestamp_path():
    ts = time.strftime("%Y%m%d_%H%M%S")
    return "{}/nao_rec_{}.wav".format(SAVE_DIR, ts)


def record_audio(nao_ip, max_duration=HARD_CAP_SEC):
    """
    Record from NAO mics with early-stop on silence.
    Returns absolute path to final WAV.
    """
    _ensure_dir(SAVE_DIR)

    # Proxies
    leds     = ALProxy("ALLeds", nao_ip, 9559)
    rec      = ALProxy("ALAudioRecorder", nao_ip, 9559)
    try:
        audio_dev = ALProxy("ALAudioDevice", nao_ip, 9559)
    except:
        audio_dev = None  # fallback to timer-only if needed

    # Optional: ALSoundDetection, more robust if available
    try:
        asd = ALProxy("ALSoundDetection", nao_ip, 9559)
        asd.setParameter("Sensitivity", 0.6)  # 0..1
        asd.subscribe("voice_cap")
        use_asd = True
    except:
        asd = None
        use_asd = False

    out_path = _timestamp_path()

    # LEDs -> green
    try: leds.fadeRGB("FaceLeds", 0.0, 1.0, 0.0, 0.1)
    except: pass

    # Start fresh & record
    try: rec.stopMicrophonesRecording()
    except: pass
    rec.startMicrophonesRecording(out_path, "wav", 16000, (0,0,1,0))

    t0 = time.time()
    heard_any = False
    last_voice_t = None

    # Poll loop
    try:
        while True:
            now = time.time()
            elapsed = now - t0

            # hard cap & min cap
            if elapsed >= max_duration:
                break

            # get energy
            energy = 0.0
            if audio_dev is not None:
                try:
                    energy = float(audio_dev.getFrontMicEnergy())
                except:
                    energy = 0.0

            # decide speaking/silence
            if energy >= ENERGY_START:
                heard_any = True
                last_voice_t = now
            else:
                # if energy below keep threshold, count as silence
                if energy <= ENERGY_KEEP and heard_any:
                    if last_voice_t is not None and (now - last_voice_t) * 1000.0 >= SILENCE_MS_STOP:
                        # Enough silence after speech -> stop
                        break

            # also stop if we've recorded at least MIN_CAP_SEC and ALSoundDetection says silence
            if use_asd and (elapsed >= MIN_CAP_SEC):
                try:
                    mem = ALProxy("ALMemory", nao_ip, 9559)
                    # ALSoundDetection/SoundDetected is True for ~few frames after a sound
                    detected = mem.getData("ALSoundDetection/SoundDetected")
                    # If nothing detected recently and we've already heard something, check for stop
                    if heard_any and not detected:
                        if last_voice_t is not None and (now - last_voice_t) * 1000.0 >= SILENCE_MS_STOP:
                            break
                except:
                    pass

            time.sleep(POLL_MS / 1000.0)

    finally:
        try: rec.stopMicrophonesRecording()
        except: pass
        # LEDs -> white
        try: leds.fadeRGB("FaceLeds", 1.0, 1.0, 1.0, 0.1)
        except: pass
        if asd:
            try: asd.unsubscribe("voice_cap")
            except: pass

    # If we somehow stopped before any speech and less than MIN_CAP_SEC, wait a blink to avoid empty file
    if (time.time() - t0) < MIN_CAP_SEC:
        time.sleep(MIN_CAP_SEC)

    # Post-process
    cleaned    = _trim_silence(out_path, TRIM_RMS_THRES, TRIM_CHUNK) or out_path
    normalized = _normalize_audio(cleaned) or cleaned
    return normalized


def _trim_silence(wav_path, threshold, chunk_size):
    try:
        wf = wave.open(wav_path, "rb")
        nchan, width, rate, nframes, comp, name = wf.getparams()
        raw = wf.readframes(nframes)
        wf.close()

        # find first loud byte
        start = 0
        end   = len(raw)

        while start < end:
            if audioop.rms(raw[start:start+chunk_size], width) > threshold:
                break
            start += chunk_size

        while end > start:
            if audioop.rms(raw[end-chunk_size:end], width) > threshold:
                break
            end -= chunk_size

        trimmed = raw[start:end] if end > start else raw
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
        if peak <= 0:
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
