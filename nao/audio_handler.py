# audio_handler.py
# -*- coding: utf-8 -*-
"""
Front-mic capture with long-silence VAD stop, trimming, pre-emphasis, AGC.
Returns final WAV path.
"""
from __future__ import print_function
import os, time, wave, struct
from naoqi import ALProxy

try:
    import audioop
except ModuleNotFoundError:
    class _AudioOpCompat(object):
        @staticmethod
        def _samples(fragment, width):
            if width != 2:
                raise ValueError("audioop fallback only supports 16-bit samples")
            count = len(fragment) // 2
            if count <= 0:
                return ()
            return struct.unpack("<{}h".format(count), fragment[:count * 2])

        @classmethod
        def rms(cls, fragment, width):
            samples = cls._samples(fragment, width)
            if not samples:
                return 0
            return int((sum(s * s for s in samples) / float(len(samples))) ** 0.5)

        @classmethod
        def max(cls, fragment, width):
            samples = cls._samples(fragment, width)
            return max(abs(s) for s in samples) if samples else 0

        @classmethod
        def mul(cls, fragment, width, factor):
            out = []
            for sample in cls._samples(fragment, width):
                value = int(sample * factor)
                if value > 32767:
                    value = 32767
                if value < -32768:
                    value = -32768
                out.append(value)
            return struct.pack("<{}h".format(len(out)), *out) if out else b""

    audioop = _AudioOpCompat()

# Paths / format
SAVE_DIR        = "/home/nao/recordings"
SAMPLE_RATE     = 16000
CHANNELS_MASK   = (0, 0, 1, 0)   # front mic mono
SAMPLE_WIDTH    = 2              # S16_LE

# Timing
CALIBRATION_MS      = 80
POLL_MS             = 30
NO_SPEECH_TIMEOUT_S = 3.0
MIN_CLIP_SEC        = 0.5        # match server-side 0.3s min — anything shorter 503s

# Stop behavior — three-tier energy classification, not binary.
#
# Real speech isn't constant amplitude. Vowels are loud, consonants/fricatives
# (s, f, th) are quiet, and there are brief sub-100ms gaps between words. The
# old binary "above keep_th = speech, below = silence" model was firing the
# trail timer on every quiet syllable, which is why mid-sentence cutoffs
# happened during continuous speech. We now classify each poll into:
#
#   SPEECH (e >= keep_th)   → reset trail, this is confident voice
#   QUIET  (e >= silent_th) → freeze trail, soft voice / breath / mouth noise
#                             still counts as "user is doing something"
#   SILENT (e <  silent_th) → advance trail toward cutoff
#
# silent_th is derived from the calibrated room-noise floor (base + small
# margin), so it adapts to the room rather than being a hard global value.
#
# Because the trail now only advances on TRUE silence (not on any sub-keep_th
# energy like before), we can be aggressive on TRAIL_MS without re-introducing
# mid-sentence cutoffs. The QUIET band catches consonants/breath/soft words
# that used to false-trigger the timer. Net post-speech delay is ~1.2s.
TRAIL_MS            = 500        # true silence required to start cutoff
GRACE_MS            = 300        # peek window before final cut
SILENT_MARGIN       = 120        # silent_th = base + this; tunes per room
SILENT_FLOOR        = 260        # absolute floor for silent_th, even in dead-quiet rooms
SILENT_RATIO        = 0.70       # silent_th will be at LEAST keep_th * this

# Backstop for the QUIET band: if we've been "quiet" (no SPEECH-level energy)
# for this long without a single keep_th crossing, the QUIET readings are
# probably room noise rather than ongoing speech. Switch to treating QUIET
# as silent so the trail can finally fire. Tighter (0.5s) so brief pauses
# between sentences ("Hey. ... Can you hear me?") don't extend the recording.
MAX_QUIET_AFTER_SPEECH_S = 0.5

# Speech-time budget AFTER onset. Cap at 10s — long enough for a thoughtful
# answer, short enough that ambient room noise can never drag a single
# capture out to 20+ seconds (which felt like NAO ignoring the user).
# Reset on every speech tick — actual continuous monologue still works.
SPEECH_MAX_SEC      = 10.0
DEFAULT_MAX_SEC     = 12.0       # wall-clock backstop including pre-onset wait
ABS_HARD_CAP_SEC    = 75.0

# Energy thresholds — tuned for the user speaking ~50cm from NAO's front mic.
# 700 catches speech-volume voices reliably without false-triggering on
# background room conversation. Drop START_BONUS if quiet voices still miss.
ENERGY_MIN_START    = 700
ENERGY_MIN_KEEP     = 420
START_BONUS         = 280
KEEP_MARGIN         = 0.55
SOFT_START_FLOOR    = 480
SOFT_START_RATIO    = 0.48
SOFT_START_MS       = 200

# Trimming. 0.25 fraction = less aggressive leading-silence strip so the
# first word/syllable isn't shaved off when users start speaking softly.
TRIM_FRACTION       = 0.25
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

def _delete_if_exists(path):
    if path and os.path.exists(path):
        try: os.unlink(path)
        except: pass

_CALIBRATE_CAP = 1800   # never let calibration push start_th above this


def _soft_start_threshold(start_th):
    return max(SOFT_START_FLOOR, int(float(start_th) * SOFT_START_RATIO))


def _calibrate_energy(ip):
    """Return (start_th, keep_th, silent_th).

    silent_th is the room-noise-aware floor below which energy is treated as
    real silence. Anything between silent_th and keep_th is "quiet" — the
    speaker is producing soft sound (breath, fricatives, soft syllables) and
    we should NOT advance the trail timer. This prevents the old behavior
    where consonants in continuous speech triggered cutoffs.
    """
    try: audio_dev = ALProxy("ALAudioDevice", ip, 9559)
    except: return ENERGY_MIN_START, ENERGY_MIN_KEEP, SILENT_FLOOR
    vals = []
    t0 = time.time()
    while (time.time() - t0)*1000.0 < CALIBRATION_MS:
        try: vals.append(float(audio_dev.getFrontMicEnergy()))
        except: vals.append(0.0)
        time.sleep(POLL_MS/1000.0)
    if not vals:
        return ENERGY_MIN_START, ENERGY_MIN_KEEP, SILENT_FLOOR
    vals.sort()
    base = vals[len(vals)//2]
    start_th = min(_CALIBRATE_CAP, max(ENERGY_MIN_START, base + START_BONUS))
    keep_th  = max(ENERGY_MIN_KEEP, start_th * KEEP_MARGIN)
    # silent_th must be HIGH ENOUGH that ambient room noise reads as silent.
    # In a noisy room the calibration base will be elevated and base+margin
    # is enough; in a quiet room base is small but we still want the silence
    # bar to be a meaningful fraction of keep_th, otherwise post-speech room
    # noise lives forever in the QUIET band and the trail never fires.
    silent_th = max(SILENT_FLOOR, base + SILENT_MARGIN, int(keep_th * SILENT_RATIO))
    # Cap silent_th so it never reaches keep_th — the QUIET band must exist.
    if silent_th >= keep_th:
        silent_th = max(SILENT_FLOOR, int(keep_th * 0.85))
    return start_th, keep_th, silent_th

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
    time.sleep(0.15)  # let recorder fully stop before restarting

    rec.startMicrophonesRecording(out_path, "wav", SAMPLE_RATE, CHANNELS_MASK)

    try:
        audio_dev = ALProxy("ALAudioDevice", nao_ip, 9559)
    except:
        audio_dev = None

    start_th, keep_th, silent_th = _calibrate_energy(nao_ip)
    soft_th = _soft_start_threshold(start_th)
    trim_rms = max(400, int(start_th * TRIM_FRACTION))
    print("[VAD] listening start_th={0:.0f} soft_th={1:.0f} keep_th={2:.0f} silent_th={3:.0f} timeout={4:.1f}s".format(
        start_th, soft_th, keep_th, silent_th, NO_SPEECH_TIMEOUT_S))

    t0 = time.time()
    heard = False
    last_voice_t = None
    speech_t0 = None              # set when onset detected; basis for SPEECH_MAX_SEC
    peak_e = 0.0
    soft_since = None

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
            if e > peak_e:
                peak_e = e
            if e >= start_th:
                heard = True
                last_voice_t = now
                speech_t0 = now
                print("[VAD] onset e={0:.0f} after {1:.2f}s".format(e, now - t0))
                break
            if e >= soft_th:
                if soft_since is None:
                    soft_since = now
                if (now - soft_since) * 1000.0 >= SOFT_START_MS:
                    heard = True
                    last_voice_t = now
                    speech_t0 = now
                    print("[VAD] soft onset e={0:.0f} after {1:.2f}s".format(e, now - t0))
                    break
            else:
                soft_since = None
            time.sleep(POLL_MS/1000.0)

        # Three-tier post-onset tracking. We separately track:
        #   last_voice_t       — last poll where energy was clearly SPEECH
        #   silence_streak_t0  — start of the current contiguous SILENT run
        #
        # Trail fires only when the SILENT streak (not "anything below keep_th")
        # exceeds TRAIL_MS. QUIET energy (between silent_th and keep_th) means
        # the user is making soft sound — breath, fricatives, "uhh" — and we
        # treat that as continuation: silence streak resets, trail does not
        # fire. This is what eliminates mid-sentence cutoffs on consonants.
        # BUT if QUIET persists for >MAX_QUIET_AFTER_SPEECH_S without any
        # keep_th hit, those readings are probably ambient room noise rather
        # than continuing speech — at that point QUIET starts counting toward
        # the silence streak so the trail can eventually fire.
        silence_streak_t0 = None
        while heard:
            now = time.time()
            if speech_t0 is not None and (now - speech_t0) >= SPEECH_MAX_SEC:
                print("[VAD] speech_max reached ({0:.1f}s)".format(now - speech_t0))
                break
            if (now - t0) >= max_duration:
                print("[VAD] hard cap reached ({0:.1f}s)".format(now - t0))
                break
            e = 0.0
            if audio_dev is not None:
                try: e = float(audio_dev.getFrontMicEnergy())
                except: e = 0.0
            if e >= keep_th:
                # SPEECH — confident voice. Reset everything.
                last_voice_t = now
                silence_streak_t0 = None
            elif e >= silent_th:
                # QUIET. Could be soft speech (consonant, breath) OR room
                # noise. Distinguish by time-since-last-real-speech: if we
                # heard a clear SPEECH-band reading recently (< MAX_QUIET_*),
                # treat as continuation. Otherwise, treat as silence so the
                # trail timer can advance and we can finally cut.
                if last_voice_t and (now - last_voice_t) < MAX_QUIET_AFTER_SPEECH_S:
                    silence_streak_t0 = None
                else:
                    if silence_streak_t0 is None:
                        silence_streak_t0 = now
                    if (now - silence_streak_t0) * 1000.0 >= TRAIL_MS:
                        total_silence_ms = (now - silence_streak_t0) * 1000.0
                        print("[VAD] cut on extended quiet ({0:.0f}ms after last SPEECH)".format(
                            (now - last_voice_t) * 1000.0))
                        # Re-use the same grace block via fall-through. The
                        # else-branch below handles SILENT → grace; we keep
                        # the structure simple by setting silence_streak_t0
                        # appropriately and letting the next iter's true-
                        # silent branch handle grace. Easier: just break.
                        # No grace — extended quiet means user is done.
                        break
            else:
                # SILENT — true quiet. Begin/continue the silence streak.
                if silence_streak_t0 is None:
                    silence_streak_t0 = now
                if (now - silence_streak_t0) * 1000.0 >= TRAIL_MS:
                    # Trail elapsed. Take a grace peek — generous now (1.5s)
                    # because the user might just be thinking with closed lips
                    # (sub-silent). We accept ANY rebound to QUIET or higher
                    # as a continuation signal, not just SPEECH.
                    grace_until = now + GRACE_MS / 1000.0
                    recovered = False
                    while time.time() < grace_until:
                        ge = 0.0
                        if audio_dev is not None:
                            try: ge = float(audio_dev.getFrontMicEnergy())
                            except: ge = 0.0
                        if ge >= silent_th:
                            # Any rebound above the silence floor is a sign
                            # the user is still doing something. Resume.
                            last_voice_t = time.time()
                            silence_streak_t0 = None
                            recovered = True
                            print("[VAD] trail recovered (e={0:.0f}, threshold>={1:.0f})".format(ge, silent_th))
                            break
                        time.sleep(POLL_MS/1000.0)
                    if not recovered:
                        total_silence_ms = (time.time() - silence_streak_t0) * 1000.0
                        print("[VAD] trail final cut after {0:.0f}ms total silence (TRAIL={1}ms + GRACE={2}ms)".format(
                            total_silence_ms, TRAIL_MS, GRACE_MS))
                        break
            time.sleep(POLL_MS/1000.0)

    finally:
        try: rec.stopMicrophonesRecording()
        except: pass
        _fade_leds(nao_ip, 1.0, 1.0, 1.0)  # done
        _robot_noise_restore(almoves)

    if not heard:
        print("[VAD] no speech detected (peak_e={0:.0f} < soft_th={1:.0f})".format(peak_e, soft_th))
        _delete_if_exists(out_path)
        return None

    dur = time.time() - t0
    if dur < MIN_CLIP_SEC:
        print("[VAD] clip too short {0:.2f}s".format(dur))
        _delete_if_exists(out_path)
        return None
    print("[VAD] captured {0:.2f}s".format(dur))

    # Skip client-side _trim_silence: it shaved off the first word/syllable
    # when users started speaking softly. Server-side Silero VAD trims the
    # WAV more accurately on the way to the transcriber.
    if PREEMPH_ENABLED:
        pre = _pre_emphasis(out_path, PREEMPH_COEFF) or out_path
    else:
        pre = out_path
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
