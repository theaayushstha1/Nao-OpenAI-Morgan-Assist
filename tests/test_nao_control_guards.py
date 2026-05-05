import importlib
import json
import sys
import time
import types
import wave
from pathlib import Path


NAO_DIR = Path(__file__).resolve().parents[1] / "nao"


def _install_fake_nao_modules(monkeypatch):
    class DummySession(object):
        def connect(self, *_args, **_kwargs):
            pass

    monkeypatch.setitem(sys.modules, "qi", types.SimpleNamespace(Session=lambda: DummySession()))
    monkeypatch.setitem(sys.modules, "naoqi", types.SimpleNamespace(ALProxy=lambda *_args, **_kwargs: None))
    monkeypatch.syspath_prepend(str(NAO_DIR))


def _reload(name):
    sys.modules.pop(name, None)
    return importlib.import_module(name)


def test_main_ignores_empty_wake_result(monkeypatch):
    _install_fake_nao_modules(monkeypatch)
    main = _reload("main")
    monkeypatch.setattr(main.config, "PROACTIVE_GREET_ENABLED", False)

    phrases = iter([None, "exit"])
    calls = []
    monkeypatch.setattr(main, "_get_phrase", lambda: next(phrases))
    monkeypatch.setattr(main.conversation, "run_streaming", lambda *_args, **_kwargs: calls.append(True))

    main.main()

    assert calls == []


def test_on_person_seen_noops_when_proactive_disabled(monkeypatch, tmp_path):
    _install_fake_nao_modules(monkeypatch)
    main = _reload("main")
    monkeypatch.setattr(main.config, "PROACTIVE_GREET_ENABLED", False)
    calls = []
    monkeypatch.setattr(main.stream_tts, "consume", lambda *_args, **_kwargs: calls.append(True))

    main._on_person_seen(str(tmp_path / "missing.jpg"))

    assert calls == []


def test_wake_word_requires_stronger_confidence(monkeypatch):
    _install_fake_nao_modules(monkeypatch)
    wake_listener = _reload("wake_listener")

    assert not wake_listener._accept_word("nao", wake_listener.NAO_WAKE_MIN_CONF, ["nao"])
    assert wake_listener._accept_word("nao", wake_listener.NAO_WAKE_MIN_CONF + 0.01, ["nao"])
    assert not wake_listener._accept_word("chat", wake_listener.MIN_CONF, ["chat"])
    assert wake_listener._accept_word("chat", wake_listener.MIN_CONF + 0.01, ["chat"])
    assert not wake_listener._accept_word(
        "morgan assist", wake_listener.MORGAN_MIN_CONF, ["morgan assist"]
    )
    assert wake_listener._accept_word(
        "morgan assist", wake_listener.MORGAN_MIN_CONF + 0.01, ["morgan assist"]
    )


def test_mininao_maps_to_skills(monkeypatch):
    _install_fake_nao_modules(monkeypatch)
    wake_listener = _reload("wake_listener")

    assert wake_listener.extract_hint("mininao") == "skills"
    assert wake_listener.extract_hint("mini-nao") == "skills"


def test_wake_listener_maps_common_mode_phrases(monkeypatch):
    _install_fake_nao_modules(monkeypatch)
    wake_listener = _reload("wake_listener")

    assert wake_listener.extract_hint("chat mode") == "chat"
    assert wake_listener.extract_hint("let's talk") == "chat"
    assert wake_listener.extract_hint("chatbot mode") == "chat"
    assert wake_listener.extract_hint("morgan") is None
    assert wake_listener.extract_hint("morgan assist") == "morgan"
    assert wake_listener.extract_hint("morgan chatbot") == "morgan"
    assert wake_listener.extract_hint("morgan state mode") == "morgan"
    assert wake_listener.extract_hint("talk to someone") == "therapy"


def test_mode_gate_requires_recent_wake(monkeypatch):
    _install_fake_nao_modules(monkeypatch)
    wake_listener = _reload("wake_listener")

    assert wake_listener._mode_gate_allows("nao", 100.0, 0.0, 0.0)
    assert not wake_listener._mode_gate_allows("morgan assist", 100.0, 90.0, 0.0)
    assert not wake_listener._mode_gate_allows("chat", 100.0, 110.0, 101.0)
    assert wake_listener._mode_gate_allows("chat", 100.0, 108.0, 99.0)


def test_record_audio_returns_none_when_no_voice(monkeypatch, tmp_path):
    _install_fake_nao_modules(monkeypatch)
    audio_handler = _reload("audio_handler")

    class FakeRecorder(object):
        def startMicrophonesRecording(self, path, *_args):
            with wave.open(path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(b"")

        def stopMicrophonesRecording(self):
            pass

    class FakeAudioDevice(object):
        def getFrontMicEnergy(self):
            return 0.0

    class FakeLeds(object):
        def fadeRGB(self, *_args):
            pass

    class FakeMoves(object):
        def setExpressiveListeningEnabled(self, *_args):
            pass

        def setBackgroundStrategy(self, *_args):
            pass

    def fake_alproxy(service, *_args):
        if service == "ALAudioRecorder":
            return FakeRecorder()
        if service == "ALAudioDevice":
            return FakeAudioDevice()
        if service == "ALLeds":
            return FakeLeds()
        if service == "ALAutonomousMoves":
            return FakeMoves()
        return None

    monkeypatch.setattr(audio_handler, "ALProxy", fake_alproxy)
    monkeypatch.setattr(audio_handler, "SAVE_DIR", str(tmp_path))
    monkeypatch.setattr(audio_handler, "NO_SPEECH_TIMEOUT_S", 0.01)
    monkeypatch.setattr(audio_handler, "CALIBRATION_MS", 1)
    monkeypatch.setattr(audio_handler, "POLL_MS", 1)

    assert audio_handler.record_audio("127.0.0.1", max_duration=0.05) is None
    assert list(tmp_path.glob("*.wav")) == []


def test_audio_soft_start_threshold_accepts_quiet_voice(monkeypatch):
    _install_fake_nao_modules(monkeypatch)
    audio_handler = _reload("audio_handler")

    assert audio_handler._soft_start_threshold(1569) <= 754
    assert audio_handler._soft_start_threshold(1500) > 650


class _FakeSseResponse(object):
    status_code = 200

    def __init__(self, events):
        self.events = events
        self.closed = False

    def iter_lines(self, decode_unicode=True):
        for ev in self.events:
            if self.closed:
                return
            yield "data: " + json.dumps(ev)

    def close(self):
        self.closed = True


class _FakeTts(object):
    def __init__(self, say_duration_s=0.2):
        self.spoken = []
        self.stopped = False
        self._duration = float(say_duration_s)

    def say(self, text):
        self.spoken.append(text)
        deadline = time.time() + self._duration
        while time.time() < deadline and not self.stopped:
            time.sleep(0.005)

    def stopAll(self):
        self.stopped = True


class _FakeAudioDevice(object):
    """Audio device whose reported energy can change over time.

    Pass a single value for a constant level, or a callable taking elapsed
    seconds (since first call) and returning the energy at that instant.
    """

    def __init__(self, energy):
        self._spec = energy
        self._t0 = None

    def getFrontMicEnergy(self):
        if callable(self._spec):
            if self._t0 is None:
                self._t0 = time.time()
            return self._spec(time.time() - self._t0)
        return self._spec


def test_stream_tts_barge_stops_stream(monkeypatch):
    _install_fake_nao_modules(monkeypatch)
    stream_tts = _reload("stream_tts")
    response = _FakeSseResponse([
        {"type": "sentence", "text": "First sentence."},
        {"type": "sentence", "text": "Second sentence."},
        {"type": "done", "active_agent": "chat", "crisis": False},
    ])
    monkeypatch.setattr(stream_tts.requests, "post", lambda *_args, **_kwargs: response)

    tts = _FakeTts()
    final = stream_tts.consume(
        "http://server/stream_turn", {}, {}, tts, lambda _a: None, lambda _i: None,
        audio_device=_FakeAudioDevice(9999),
        barge_config={"enabled": True, "threshold": 1000,
                      "sustain_ms": 0, "deadzone_ms": 0, "poll_ms": 1},
    )

    assert final["barge_in"] is True
    assert response.closed is True
    assert tts.stopped is True
    assert len(tts.spoken) == 1


def test_stream_tts_no_barge_without_audio_device(monkeypatch):
    _install_fake_nao_modules(monkeypatch)
    stream_tts = _reload("stream_tts")
    response = _FakeSseResponse([
        {"type": "sentence", "text": "First sentence."},
        {"type": "sentence", "text": "Second sentence."},
        {"type": "done", "active_agent": "chat", "crisis": False},
    ])
    monkeypatch.setattr(stream_tts.requests, "post", lambda *_args, **_kwargs: response)

    tts = _FakeTts()
    final = stream_tts.consume(
        "http://server/stream_turn", {}, {}, tts, lambda _a: None, lambda _i: None,
    )

    assert final["active_agent"] == "chat"
    assert response.closed is False
    assert len(tts.spoken) == 2


def test_stream_tts_deadzone_suppresses_early_barge(monkeypatch):
    """Energy at threshold during the deadzone window must NOT interrupt.

    This is what protects us from NAO's own loudest speaker output triggering
    a barge in the first ~700ms of every sentence.
    """
    _install_fake_nao_modules(monkeypatch)
    stream_tts = _reload("stream_tts")
    response = _FakeSseResponse([
        {"type": "sentence", "text": "Whole first sentence."},
        {"type": "done", "active_agent": "chat", "crisis": False},
    ])
    monkeypatch.setattr(stream_tts.requests, "post", lambda *_args, **_kwargs: response)

    # Loud the whole time — only a working deadzone can save us here.
    tts = _FakeTts(say_duration_s=0.10)
    final = stream_tts.consume(
        "http://server/stream_turn", {}, {}, tts, lambda _a: None, lambda _i: None,
        audio_device=_FakeAudioDevice(9999),
        # Deadzone exceeds the time tts.say() will block, so barge cannot fire.
        barge_config={"enabled": True, "threshold": 1000,
                      "sustain_ms": 0, "deadzone_ms": 500, "poll_ms": 5},
    )

    assert final.get("barge_in") is not True
    assert tts.stopped is False
    assert tts.spoken == ["Whole first sentence."]


def test_stream_tts_sustain_rejects_transient_spike(monkeypatch):
    """A short energy spike that doesn't sustain must NOT interrupt."""
    _install_fake_nao_modules(monkeypatch)
    stream_tts = _reload("stream_tts")
    response = _FakeSseResponse([
        {"type": "sentence", "text": "Quiet sentence."},
        {"type": "done", "active_agent": "chat", "crisis": False},
    ])
    monkeypatch.setattr(stream_tts.requests, "post", lambda *_args, **_kwargs: response)

    # Energy spikes for 50ms then returns to silence — under the 200ms sustain.
    def energy_at(elapsed_s):
        return 9999 if 0.02 <= elapsed_s <= 0.07 else 100

    tts = _FakeTts(say_duration_s=0.30)
    final = stream_tts.consume(
        "http://server/stream_turn", {}, {}, tts, lambda _a: None, lambda _i: None,
        audio_device=_FakeAudioDevice(energy_at),
        barge_config={"enabled": True, "threshold": 1000,
                      "sustain_ms": 200, "deadzone_ms": 0, "poll_ms": 5},
    )

    assert final.get("barge_in") is not True
    assert tts.stopped is False
    assert tts.spoken == ["Quiet sentence."]


class _FakePlaybackState(object):
    def __init__(self, active=True, age_ms=2000):
        self.active = active
        self.age_ms = age_ms

    def is_playing_or_recent(self, _tail_s, now=None):
        return self.active

    def playback_age_ms(self, now=None):
        return self.age_ms


def test_realtime_echo_gate_suppresses_mic_during_playback(monkeypatch):
    _install_fake_nao_modules(monkeypatch)
    realtime_chat = _reload("realtime_chat")

    gate = realtime_chat._EchoGate(
        enabled=True, acoustic_barge_enabled=False, threshold=1000,
        sustain_ms=0, deadzone_ms=0, tail_ms=900,
    )

    assert gate.check(_FakePlaybackState(active=True), rms=9999, now=1.0) == (False, False)
    assert gate.check(_FakePlaybackState(active=False), rms=9999, now=1.0) == (True, False)
    assert gate.check(_FakePlaybackState(active=False), rms=100, now=1.0,
                      assistant_active=True) == (False, False)


def test_realtime_echo_gate_requires_sustained_acoustic_barge(monkeypatch):
    _install_fake_nao_modules(monkeypatch)
    realtime_chat = _reload("realtime_chat")
    gate = realtime_chat._EchoGate(
        enabled=True, acoustic_barge_enabled=True, threshold=1000,
        sustain_ms=200, deadzone_ms=0, tail_ms=900,
    )
    player = _FakePlaybackState(active=True, age_ms=2000)

    assert gate.check(player, rms=9999, now=1.00) == (False, False)
    assert gate.check(player, rms=9999, now=1.10) == (False, False)
    assert gate.check(player, rms=9999, now=1.25) == (True, True)


def test_realtime_player_pending_queue_counts_as_echo_active(monkeypatch):
    _install_fake_nao_modules(monkeypatch)
    realtime_chat = _reload("realtime_chat")

    player = realtime_chat._PlayerThread("127.0.0.1", 9559)
    player.enqueue(b"\x00\x00" * 100)

    assert player.is_playing_or_recent(0.1, now=time.time()) is True


def test_realtime_config_defaults_when_old_config_missing(monkeypatch):
    _install_fake_nao_modules(monkeypatch)
    realtime_chat = _reload("realtime_chat")

    assert realtime_chat._cfg_int("MISSING_INT_SETTING", 5050) == 5050
    assert realtime_chat._cfg_float("MISSING_FLOAT_SETTING", 0.3) == 0.3
    assert realtime_chat._cfg_bool("MISSING_BOOL_SETTING", True) is True


def test_realtime_cancel_errors_do_not_exit_chat_mode(monkeypatch):
    _install_fake_nao_modules(monkeypatch)
    realtime_chat = _reload("realtime_chat")

    ev = {
        "type": "error",
        "error": {
            "code": "response_cancel_not_active",
            "message": "Cancellation failed: no active response found",
        },
    }

    assert realtime_chat._recoverable_realtime_error(ev) is True


def test_intent_does_not_exit_on_long_stop_complaint(monkeypatch):
    _install_fake_nao_modules(monkeypatch)
    intent = _reload("utils.intent")
    exit_detection = _reload("utils.exit_detection")
    text = (
        "The AI is better because it is not listening as VAD. "
        "Make it better so it only talks when the speaker is talking, "
        "not detect the noise and stop me."
    )

    assert intent.detect(text, current_mode="chat") is None
    assert exit_detection.detect_exit_intent(text) is False
