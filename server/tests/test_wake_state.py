"""Phase 3 unit tests — Hybrid Wake state machine.

These tests exercise the contract laid out in ``docs/PHASE_3_TASK_MAP.md``
(``WakeStateMachine`` in ``nao/wake_state.py``):

    IDLE -> AWARE -> ENGAGED -> LISTENING -> SPEAKING

The state machine is owned by a sibling Phase 3 agent in a separate
worktree and may not have landed in the merge yet. Every test guards
``nao.wake_state`` with ``pytest.importorskip(...)`` plus targeted
``hasattr(...)`` / signature checks, so the file collects clean even
when the implementation is missing — once the new ``WakeStateMachine``
ships, the existing test bodies start exercising it.

Heavy mocking is mandatory: naoqi is never imported, no robot, no real
LEDs, no real ALFaceDetection. ``face_naoqi.detect_faces_with_geometry``
is monkeypatched to feed synthetic per-frame detection events; the
``AdaptiveVad`` class is replaced with a fake that toggles a "speech
onset" callback; ``WakeListener`` is replaced with a fake that returns a
keyword on demand; the ``LedDriver`` is replaced with a recorder that
captures every color transition.

The behavioural contract under test comes verbatim from
``docs/PHASE_3_TASK_MAP.md`` § "Wake state machine — exact contract".
"""
from __future__ import annotations

import sys
import time
import types
from typing import Any, Callable

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Naoqi stubbing — installed once per test via ``_stub_naoqi`` so any module
# that tries to ``from naoqi import ALProxy`` at import time succeeds without
# the real robot SDK being on PYTHONPATH.
# ─────────────────────────────────────────────────────────────────────────────


def _stub_naoqi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install minimal ``qi`` + ``naoqi`` stand-ins in ``sys.modules``.

    The stubs are intentionally inert — they expose the names the wake
    state machine reaches for (``ALProxy``, ``qi.Session``) but every
    method is a no-op. Tests use ``monkeypatch.setattr`` on
    ``nao.wake_state`` to swap in test doubles for the *external*
    collaborators (face detector, VAD, wake listener, LEDs) — the naoqi
    services themselves are never called.
    """

    class _DummySession:
        def connect(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def service(self, _name: str) -> Any:  # pragma: no cover - defensive
            return types.SimpleNamespace()

    qi_stub = types.SimpleNamespace(Session=lambda: _DummySession())
    naoqi_stub = types.SimpleNamespace(ALProxy=lambda *_a, **_k: types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "qi", qi_stub)
    monkeypatch.setitem(sys.modules, "naoqi", naoqi_stub)


# ─────────────────────────────────────────────────────────────────────────────
# Test doubles for the four collaborator types the state machine consumes.
# ─────────────────────────────────────────────────────────────────────────────


class FakeLed:
    """Recorder for ``LedDriver`` calls.

    Exposes the same attribute-shaped API documented in the task map:
    ``set_idle()``, ``set_aware()``, ``set_engaged()``, ``set_listening()``,
    ``set_speaking()``, ``chime()``. Every call is appended to ``self.calls``
    so tests can assert the exact transition sequence.
    """

    EYES_GROUP = "FaceLeds"
    CHEST_GROUP = "ChestLeds"
    EAR_LEFT_GROUP = "EarLeds"

    # Mirror the documented color presets so test bodies can compare RGB
    # tuples if they want to look past the convenience wrappers.
    COLOR_GRAY = (0.10, 0.10, 0.12)
    COLOR_SOFT_BLUE = (0.10, 0.30, 0.70)
    COLOR_SOLID_BLUE = (0.20, 0.50, 1.00)
    COLOR_CYAN = (0.10, 0.80, 0.95)
    COLOR_YELLOW = (1.00, 0.80, 0.10)
    COLOR_GREEN = (0.10, 0.90, 0.30)

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def fade(self, group: str, rgb: tuple[float, float, float], duration_s: float = 0.4) -> None:
        self.calls.append(("fade", (group, rgb, duration_s)))

    def pulse(self, group: str, rgb: tuple[float, float, float],
              period_s: float = 1.0, count: int | None = None) -> None:
        self.calls.append(("pulse", (group, rgb, period_s, count)))

    def set_idle(self) -> None:
        self.calls.append(("set_idle", ()))

    def set_aware(self) -> None:
        self.calls.append(("set_aware", ()))

    def set_engaged(self) -> None:
        self.calls.append(("set_engaged", ()))

    def set_listening(self) -> None:
        self.calls.append(("set_listening", ()))

    def set_speaking(self) -> None:
        self.calls.append(("set_speaking", ()))

    def chime(self) -> None:
        self.calls.append(("chime", ()))

    # Convenience helpers for tests
    def color_calls(self) -> list[str]:
        return [name for name, _args in self.calls]


class FakeWakeListener:
    """Stand-in for ``wake_listener.WakeListener``.

    The real listener is a NAOqi-driven blocking loop; the test double
    is a passive object whose ``poll()`` (or whatever the wake state
    machine actually calls — duck-typing keeps this resilient) returns
    a pre-seeded result. Tests inject the keyword by setting
    ``self.next_keyword`` before the relevant tick.
    """

    def __init__(self) -> None:
        self.next_keyword: str | None = None
        self.poll_count = 0

    def poll(self) -> str | None:
        self.poll_count += 1
        result = self.next_keyword
        # One-shot: clear after delivery so the next poll returns None
        # unless the test re-arms it.
        self.next_keyword = None
        return result

    # Some implementations may use a callback registration pattern; expose
    # both shapes so the state machine can pick whichever it likes.
    def heard_keyword(self) -> str | None:
        return self.poll()


class FakeAdaptiveVad:
    """Stand-in for ``audio_handler.AdaptiveVad``.

    Exposes a ``speech_active`` flag (read by ``WakeStateMachine``) plus
    an ``on_speech_start`` callback registration; both shapes are
    plausible from the task map ("speech onset detected by AdaptiveVad").
    Tests flip ``speech_active = True`` at the moment they want the
    speech-onset gate to fire.
    """

    def __init__(self) -> None:
        self.speech_active = False
        self._start_cb: Callable[[], None] | None = None
        self._end_cb: Callable[[dict], None] | None = None
        self.calibrated = False
        self.running = False

    def calibrate(self, _audio_proxy: Any, seconds: float = 0.8) -> dict:
        self.calibrated = True
        return {"start_th": 0.05, "keep_th": 0.04, "silent_th": 0.02, "ambient_floor": 0.01}

    def run(self, _audio_proxy: Any, on_speech_start: Callable[[], None] | None = None,
            on_speech_end: Callable[[dict], None] | None = None) -> None:
        # Don't actually loop — the state machine drives ticks itself.
        self._start_cb = on_speech_start
        self._end_cb = on_speech_end
        self.running = True

    def stop(self) -> None:
        self.running = False

    def fire_speech_start(self) -> None:
        if self._start_cb is not None:
            self._start_cb()
        self.speech_active = True


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic face-detection event helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_face(face_id: str = "face_a", confidence: float = 0.5,
               distance_m: float = 1.2, yaw_deg: float = 0.0,
               pitch_deg: float = 0.0, name: str | None = None) -> dict:
    """Build the dict shape documented in the task map for
    ``detect_faces_with_geometry``."""
    return {
        "face_id": face_id,
        "name": name,
        "confidence": float(confidence),
        "distance_m": float(distance_m),
        "yaw_deg": float(yaw_deg),
        "pitch_deg": float(pitch_deg),
    }


def _scripted_detector(scripts: list[list[dict]]) -> Callable[..., list[dict]]:
    """Return a callable that pops the next pre-canned detection list.

    Each call advances one script step; once exhausted, returns ``[]``
    (face lost) for every subsequent call. This lets a test feed a
    deterministic timeline of frames without a real camera.
    """
    cursor = {"i": 0}

    def _detect(*_args: Any, **_kwargs: Any) -> list[dict]:
        i = cursor["i"]
        cursor["i"] = i + 1
        if i < len(scripts):
            return list(scripts[i])
        return []

    return _detect


# ─────────────────────────────────────────────────────────────────────────────
# Common test fixture — instantiate the WakeStateMachine with all
# collaborators replaced. Skips cleanly when ``nao.wake_state`` doesn't
# exist yet.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def wake_machine_kit(monkeypatch: pytest.MonkeyPatch):
    """Yield ``(machine, leds, vad, listener, callbacks, set_script, set_clock)``.

    ``set_script(frames)`` programs the synthetic face detector with a
    list of per-tick detection lists. ``set_clock(t)`` advances the
    wake state machine's monotonic clock so timeout tests don't have to
    sleep for real wall-clock seconds.
    """
    _stub_naoqi(monkeypatch)
    pytest.importorskip("nao.wake_state")
    from nao import wake_state as ws_mod

    if not hasattr(ws_mod, "WakeStateMachine"):
        pytest.skip("WakeStateMachine class not implemented yet")

    leds = FakeLed()
    vad = FakeAdaptiveVad()
    listener = FakeWakeListener()

    callbacks = {
        "engaged": [],
        "lost": [],
        "listening": [],
        "speaking_done": [],
    }

    def _on_engaged(face_id: str, gate_name: str, confidence: float, distance_m: float) -> None:
        callbacks["engaged"].append({
            "face_id": face_id,
            "gate": gate_name,
            "confidence": confidence,
            "distance_m": distance_m,
        })

    def _on_lost() -> None:
        callbacks["lost"].append(True)

    def _on_listening() -> None:
        callbacks["listening"].append(True)

    def _on_speaking_done() -> None:
        callbacks["speaking_done"].append(True)

    # Patch the face detector function before the state machine spins up
    # so subscription / first-tick logic uses our scripted feed.
    pytest.importorskip("nao.utils.face_naoqi")
    from nao.utils import face_naoqi  # type: ignore[import-not-found]
    detect_script: dict = {"fn": lambda *a, **k: []}

    def _detector(*args: Any, **kwargs: Any) -> list[dict]:
        return detect_script["fn"](*args, **kwargs)

    monkeypatch.setattr(face_naoqi, "detect_faces_with_geometry", _detector, raising=False)

    # The state machine may also use ``closest_face`` and
    # ``is_mutually_gazing`` helpers (Deliverable B). Provide
    # default-correct stubs in case those haven't landed yet.
    if not hasattr(face_naoqi, "closest_face"):
        def _closest(faces: list[dict]) -> dict | None:
            if not faces:
                return None
            return sorted(
                faces,
                key=lambda f: (f.get("distance_m", 999.0), -float(f.get("confidence", 0.0))),
            )[0]

        monkeypatch.setattr(face_naoqi, "closest_face", _closest, raising=False)

    if not hasattr(face_naoqi, "is_mutually_gazing"):
        def _gaze(face: dict, yaw_tolerance_deg: float = 15.0,
                  pitch_tolerance_deg: float = 15.0) -> bool:
            return (abs(face.get("yaw_deg", 999.0)) <= yaw_tolerance_deg
                    and abs(face.get("pitch_deg", 999.0)) <= pitch_tolerance_deg)

        monkeypatch.setattr(face_naoqi, "is_mutually_gazing", _gaze, raising=False)

    # Construct the machine. The constructor signature in the task map
    # has many keyword args; supply the documented defaults so a future
    # signature tweak (extra optional args) doesn't break the harness.
    init_kwargs: dict[str, Any] = dict(
        nao_ip="127.0.0.1",
        nao_port=9559,
        leds=leds,
        fallback_word_listener=listener,
        on_engaged=_on_engaged,
        on_lost=_on_lost,
        on_listening=_on_listening,
        on_speaking_done=_on_speaking_done,
        face_min_conf=0.35,
        face_max_distance_m=1.5,
        face_max_angle_deg=60.0,
        aware_timeout_s=8.0,
        gaze_required_s=1.5,
        proximity_required_s=1.0,
        sustained_conf=0.5,
        sustained_required_s=2.0,
        sustained_angle_deg=30.0,
    )

    try:
        machine = ws_mod.WakeStateMachine(**init_kwargs)
    except TypeError:
        # Implementation may have a slightly different constructor — try
        # the minimum set documented as required.
        machine = ws_mod.WakeStateMachine(
            nao_ip="127.0.0.1",
            nao_port=9559,
            leds=leds,
            fallback_word_listener=listener,
            on_engaged=_on_engaged,
            on_lost=_on_lost,
            on_listening=_on_listening,
            on_speaking_done=_on_speaking_done,
        )

    # Wire the VAD if the machine exposes a setter or attribute.
    if hasattr(machine, "vad"):
        try:
            machine.vad = vad
        except AttributeError:
            pass
    if hasattr(machine, "set_vad"):
        try:
            machine.set_vad(vad)
        except Exception:
            pass

    # Many state machines run a tick loop in a thread; tests want to
    # drive the loop deterministically. Patch ``time.monotonic`` and
    # ``time.time`` on the wake_state module if it imported them at
    # module scope.
    fake_clock = {"now": 1000.0}

    def _now() -> float:
        return float(fake_clock["now"])

    monkeypatch.setattr(ws_mod, "time", types.SimpleNamespace(
        time=_now, monotonic=_now, sleep=lambda *_a, **_k: None,
    ), raising=False)

    def _set_script(frames: list[list[dict]]) -> None:
        detect_script["fn"] = _scripted_detector(frames)

    def _set_clock(t: float) -> None:
        fake_clock["now"] = float(t)

    yield (machine, leds, vad, listener, callbacks, _set_script, _set_clock)

    # Best-effort cleanup
    try:
        machine.stop()
    except Exception:
        pass


def _tick(machine: Any, count: int = 1) -> None:
    """Drive the state machine forward by ``count`` synthetic ticks.

    Implementations that expose a ``tick()`` / ``_tick()`` / ``step()``
    method get called directly; otherwise the harness falls back to
    invoking the public ``start()`` for one cycle then ``stop()``.
    """
    candidates = ("tick", "step", "_tick", "_step", "process", "_process")
    for name in candidates:
        fn = getattr(machine, name, None)
        if callable(fn):
            for _ in range(count):
                fn()
            return
    # Last resort: ``start()`` is allowed to be blocking, so this is only
    # safe in a thread. Tests fall back to skipping if no tick hook
    # exists.
    pytest.skip("WakeStateMachine has no public tick/step hook for "
                "deterministic testing")


def _state(machine: Any) -> str:
    """Read the current state through whichever accessor the machine exposes."""
    if hasattr(machine, "current_state"):
        try:
            return str(machine.current_state())
        except TypeError:
            return str(machine.current_state)
    if hasattr(machine, "state"):
        return str(machine.state)
    pytest.skip("WakeStateMachine has no current_state() / .state accessor")


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_idle_to_aware_on_face_detection(wake_machine_kit):
    """Face conf >= 0.35 AND distance <= 1.5 m -> AWARE."""
    machine, leds, _vad, _listener, _cb, set_script, set_clock = wake_machine_kit

    # First frame: detectable face just inside the gating envelope.
    set_script([[_make_face(face_id="f1", confidence=0.42, distance_m=1.4,
                             yaw_deg=10.0, pitch_deg=5.0)]])
    set_clock(1000.0)
    _tick(machine)

    assert _state(machine) == "AWARE", (
        "expected AWARE after a face within the gating envelope; "
        "got %r (LED calls: %r)" % (_state(machine), leds.color_calls())
    )
    # Soft-blue eye fade is the documented animacy cue. We accept any
    # ``set_aware`` / ``fade`` to ``COLOR_SOFT_BLUE`` as evidence.
    assert any(name == "set_aware" for name, _ in leds.calls) or any(
        name == "fade" and args[1] == FakeLed.COLOR_SOFT_BLUE for name, args in leds.calls
    ), "AWARE entry must trigger the soft-blue eye color"
    # No chime in AWARE — only ENGAGED triggers the chime.
    assert not any(name == "chime" for name, _ in leds.calls), (
        "AWARE must not chime — chime is only for ENGAGED"
    )


def test_aware_timeout_returns_to_idle_silently(wake_machine_kit):
    """After 8 s in AWARE with no engagement gate, drop to IDLE silently.

    "Silently" means: no chime, no greeting callback, no engaged callback.
    """
    machine, leds, _vad, _listener, callbacks, set_script, set_clock = wake_machine_kit

    # Single face frame to enter AWARE — perpendicular angle so no gate fires.
    set_script([
        [_make_face(face_id="f1", confidence=0.40, distance_m=1.4,
                    yaw_deg=45.0, pitch_deg=10.0)],
        [_make_face(face_id="f1", confidence=0.40, distance_m=1.4,
                    yaw_deg=45.0, pitch_deg=10.0)],
        [],  # face lost / out of frame
    ])
    set_clock(1000.0)
    _tick(machine)
    assert _state(machine) == "AWARE"

    # Advance the clock past the 8 s aware timeout.
    set_clock(1009.0)
    _tick(machine, count=3)

    assert _state(machine) == "IDLE", (
        "expected IDLE after 8 s aware timeout; got %r" % _state(machine)
    )
    assert callbacks["engaged"] == [], (
        "AWARE timeout must not fire on_engaged"
    )
    assert not any(name == "chime" for name, _ in leds.calls), (
        "AWARE timeout must be silent — no chime"
    )


def test_aware_to_engaged_on_mutual_gaze(wake_machine_kit):
    """1.5 s sustained mutual gaze -> ENGAGED with gate='mutual_gaze'."""
    machine, leds, _vad, _listener, callbacks, set_script, set_clock = wake_machine_kit

    # Frontal face (yaw, pitch ≈ 0) sustained across multiple ticks at
    # 30 fps. Each script entry advances ~33 ms in the real loop; tests
    # advance the synthetic clock by 1.6 s to clear the 1.5 s threshold.
    frontal = _make_face(face_id="f1", confidence=0.55, distance_m=1.2,
                         yaw_deg=2.0, pitch_deg=1.0)
    set_script([[frontal]] * 60)
    set_clock(1000.0)
    _tick(machine)
    assert _state(machine) == "AWARE"

    set_clock(1001.6)
    _tick(machine, count=5)

    assert _state(machine) == "ENGAGED", (
        "expected ENGAGED after 1.5 s mutual gaze; got %r" % _state(machine)
    )
    assert len(callbacks["engaged"]) == 1, "on_engaged must fire exactly once"
    assert callbacks["engaged"][0]["gate"] in ("mutual_gaze", "gaze"), (
        "engagement gate must be reported as mutual_gaze; got %r"
        % callbacks["engaged"][0]["gate"]
    )
    assert callbacks["engaged"][0]["face_id"] == "f1"


def test_aware_to_engaged_on_proximity(wake_machine_kit):
    """Distance < 1.0 m stable for 1.0 s -> ENGAGED with gate='proximity'."""
    machine, _leds, _vad, _listener, callbacks, set_script, set_clock = wake_machine_kit

    # Face arrives at 0.7 m and holds steady — yaw is non-frontal so the
    # gaze gate doesn't fire first.
    near = _make_face(face_id="f2", confidence=0.50, distance_m=0.7,
                      yaw_deg=20.0, pitch_deg=10.0)
    set_script([[near]] * 50)
    set_clock(2000.0)
    _tick(machine)
    assert _state(machine) == "AWARE"

    set_clock(2001.1)
    _tick(machine, count=5)

    assert _state(machine) == "ENGAGED", (
        "expected ENGAGED after proximity stable; got %r" % _state(machine)
    )
    assert callbacks["engaged"][0]["gate"] in ("proximity", "near", "distance"), (
        "engagement gate must be reported as proximity; got %r"
        % callbacks["engaged"][0]["gate"]
    )
    assert callbacks["engaged"][0]["distance_m"] < 1.0


def test_aware_to_engaged_on_sustained_face(wake_machine_kit):
    """Conf >= 0.5 sustained 2 s with frontal angle -> ENGAGED."""
    machine, _leds, _vad, _listener, callbacks, set_script, set_clock = wake_machine_kit

    # 2.1 s of strong, frontal-but-far face. Distance is > 1.0 m so the
    # proximity gate does NOT fire, but yaw is 5° so the sustained_face
    # gate (which uses the ±30° angle threshold) is the one that wins.
    frontal_far = _make_face(face_id="f3", confidence=0.65, distance_m=1.3,
                             yaw_deg=5.0, pitch_deg=2.0)
    set_script([[frontal_far]] * 80)
    set_clock(3000.0)
    _tick(machine)
    assert _state(machine) == "AWARE"

    set_clock(3002.1)
    _tick(machine, count=8)

    assert _state(machine) == "ENGAGED", (
        "expected ENGAGED after sustained_face; got %r" % _state(machine)
    )
    # Implementations may report ``mutual_gaze`` here too because at 5°
    # the gaze gate likely also fires; both are valid per the task map.
    assert callbacks["engaged"][0]["gate"] in (
        "sustained_face", "sustained", "mutual_gaze", "gaze",
    ), ("expected sustained_face/mutual_gaze gate; got %r"
        % callbacks["engaged"][0]["gate"])


def test_aware_to_engaged_on_speech_onset(wake_machine_kit):
    """AdaptiveVad fires speech-start callback while AWARE -> ENGAGED."""
    machine, _leds, vad, _listener, callbacks, set_script, set_clock = wake_machine_kit

    set_script([
        [_make_face(face_id="f4", confidence=0.45, distance_m=1.2,
                    yaw_deg=40.0, pitch_deg=10.0)],
    ])
    set_clock(4000.0)
    _tick(machine)
    assert _state(machine) == "AWARE"

    # Speech-onset event from the user (e.g. they started talking before
    # any other engagement gate). State machine should engage immediately.
    vad.fire_speech_start()
    set_clock(4000.1)
    _tick(machine, count=2)

    assert _state(machine) == "ENGAGED", (
        "expected ENGAGED after speech onset; got %r" % _state(machine)
    )
    assert callbacks["engaged"][0]["gate"] in ("speech", "speech_onset", "vad"), (
        "engagement gate must be reported as speech; got %r"
        % callbacks["engaged"][0]["gate"]
    )


def test_aware_to_engaged_on_keyword_fallback(wake_machine_kit):
    """'hey nao' keyword fallback -> ENGAGED with gate='keyword'."""
    machine, _leds, _vad, listener, callbacks, set_script, set_clock = wake_machine_kit

    set_script([
        [_make_face(face_id="f5", confidence=0.40, distance_m=1.4,
                    yaw_deg=30.0, pitch_deg=15.0)],
    ])
    set_clock(5000.0)
    _tick(machine)
    assert _state(machine) == "AWARE"

    # Arm the wake-listener fake to deliver "hey nao" on the next poll.
    listener.next_keyword = "hey nao"
    set_clock(5000.1)
    _tick(machine, count=2)

    assert _state(machine) == "ENGAGED", (
        "expected ENGAGED after keyword fallback; got %r" % _state(machine)
    )
    assert callbacks["engaged"][0]["gate"] == "keyword", (
        "engagement gate must be 'keyword'; got %r"
        % callbacks["engaged"][0]["gate"]
    )


def test_passerby_does_not_engage(wake_machine_kit):
    """A face at 2 m perpendicular that never sustains stays AWARE then drops to IDLE.

    This is the headline false-wake protection from the PRD verification table:
    "10 trials walking past at 2 m perpendicular without stopping → zero
    AWARE→ENGAGED transitions".
    """
    machine, _leds, _vad, _listener, callbacks, set_script, set_clock = wake_machine_kit

    # Phase 1: passerby briefly visible at 1.4 m sideways. ALFaceDetection
    # may briefly latch onto them — note the angle is the gating cutoff
    # (60°) but their face is angled away. In some implementations the
    # face won't even count as "valid" past 1.5 m; the test feeds 1.4 m
    # so we're inside the IDLE→AWARE envelope but the engagement gates
    # never fire.
    passerby = _make_face(face_id="passerby", confidence=0.40,
                          distance_m=1.4, yaw_deg=55.0, pitch_deg=20.0)
    # Brief glimpse — only a few frames before they're past.
    set_script([[passerby]] * 5 + [[]] * 30)
    set_clock(6000.0)
    _tick(machine, count=3)

    # We may end up in AWARE briefly, that's allowed. What matters is
    # we never escalate to ENGAGED.
    assert _state(machine) in ("IDLE", "AWARE"), (
        "passerby must not push us beyond AWARE; got %r" % _state(machine)
    )
    assert callbacks["engaged"] == [], (
        "passerby must trigger zero ENGAGED transitions; got %r"
        % callbacks["engaged"]
    )

    # Advance past the 8 s aware timeout — we must drop back to IDLE.
    set_clock(6010.0)
    _tick(machine, count=5)

    assert _state(machine) == "IDLE", (
        "expected IDLE after passerby + aware timeout; got %r" % _state(machine)
    )
    assert callbacks["engaged"] == [], (
        "still must have zero ENGAGED transitions after timeout"
    )


def test_engaged_to_listening_after_callback(wake_machine_kit):
    """``on_engaged`` fires once with face_id, gate, confidence, distance_m
    and the machine then transitions into LISTENING.

    The contract from the task map: ``on_engaged(face_id, gate_name,
    confidence, distance_m) — called once per session``.
    """
    machine, leds, _vad, _listener, callbacks, set_script, set_clock = wake_machine_kit

    # Enter AWARE -> ENGAGED via a clean mutual-gaze trigger.
    frontal = _make_face(face_id="known_user", confidence=0.62,
                         distance_m=0.9, yaw_deg=2.0, pitch_deg=1.0,
                         name="Alice")
    set_script([[frontal]] * 60)
    set_clock(7000.0)
    _tick(machine)
    set_clock(7001.6)
    _tick(machine, count=5)

    assert _state(machine) == "ENGAGED"
    assert len(callbacks["engaged"]) == 1, "on_engaged must fire exactly once"
    args = callbacks["engaged"][0]
    assert args["face_id"] == "known_user"
    assert args["gate"] in ("mutual_gaze", "gaze")
    assert 0.0 < args["confidence"] <= 1.0
    assert 0.0 < args["distance_m"] <= 1.5

    # Continue ticking — the machine should walk ENGAGED -> LISTENING.
    # This is driven by the server's ``audio_chunk`` arrival in
    # production, but tests substitute the public transition hook.
    if hasattr(machine, "set_state"):
        machine.set_state("LISTENING")
    elif hasattr(machine, "transition_to"):
        machine.transition_to("LISTENING")
    else:
        # Some implementations auto-advance when the WS session opens.
        # Fall back to one more tick and accept either ENGAGED or
        # LISTENING — the bell of this test is the on_engaged callback.
        _tick(machine, count=1)
        assert _state(machine) in ("ENGAGED", "LISTENING")
        return

    set_clock(7002.0)
    _tick(machine, count=2)
    assert _state(machine) == "LISTENING"
    # ``on_listening`` should have fired by now.
    assert callbacks["listening"], "on_listening must fire on LISTENING entry"
    # And the listening LED color should appear in the recorder.
    assert any(name == "set_listening" for name, _ in leds.calls) or any(
        name == "fade" and args[1] == FakeLed.COLOR_CYAN for name, args in leds.calls
    ), "LISTENING entry must trigger the cyan eye color"


def test_face_lost_in_listening_holds_5s(wake_machine_kit):
    """If face is lost mid-LISTENING, hold the state for 5 s before fallback.

    From the PRD: "Failure recovery — face lost mid-conversation: hold
    LISTENING for 5 s; if not restored, soft 'I've lost sight of you'
    then IDLE." We don't assert on the soft-prompt TTS here (that's the
    main-rewire agent's territory); we assert that the state HOLDS in
    LISTENING for the 5 s grace window.
    """
    machine, _leds, _vad, _listener, _cb, set_script, set_clock = wake_machine_kit

    # Walk into LISTENING via the same gaze gate as the previous test.
    frontal = _make_face(face_id="f7", confidence=0.55, distance_m=1.0,
                         yaw_deg=3.0, pitch_deg=2.0)
    set_script([[frontal]] * 100 + [[]] * 200)
    set_clock(8000.0)
    _tick(machine)
    set_clock(8001.6)
    _tick(machine, count=5)

    if hasattr(machine, "set_state"):
        machine.set_state("LISTENING")
    elif hasattr(machine, "transition_to"):
        machine.transition_to("LISTENING")
    else:
        # Without an explicit transition hook we can't reliably enter
        # LISTENING from the test — skip the rest. The other tests in
        # the file still cover the AWARE/ENGAGED contract.
        pytest.skip("WakeStateMachine has no transition hook for LISTENING")

    set_clock(8001.7)
    _tick(machine, count=2)
    assert _state(machine) == "LISTENING"

    # Face lost — but only 2 s have passed; LISTENING must hold.
    set_clock(8003.7)
    _tick(machine, count=2)
    assert _state(machine) == "LISTENING", (
        "LISTENING must hold for 5 s after face loss; got %r at +2 s"
        % _state(machine)
    )

    # 4.9 s in: still holding.
    set_clock(8006.6)
    _tick(machine, count=2)
    assert _state(machine) == "LISTENING", (
        "LISTENING must still hold at +4.9 s; got %r" % _state(machine)
    )

    # Past the 5 s grace — implementations may either drop to IDLE
    # immediately or first emit a recovery prompt and then drop. Either
    # outcome is acceptable here; we just check we eventually leave
    # LISTENING.
    set_clock(8010.0)
    _tick(machine, count=5)
    assert _state(machine) != "LISTENING", (
        "after 5 s without face restoration we must leave LISTENING"
    )
