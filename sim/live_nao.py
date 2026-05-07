"""Virtual NAO — interactive Mac entry point for the Phase 10.5 simulator.

Drives the entire NAO ↔ server voice pipeline from a developer Mac.

Boot sequence
-------------
1. Install ``sim.fake_naoqi`` into ``sys.modules`` so any subsequent
   ``import naoqi`` / ``import qi`` from ``nao/*`` resolves to our fakes.
2. Set the env vars the robot side reads at import time
   (``NAO_IP=127.0.0.1``, ``SERVER_IP=127.0.0.1``, ``USE_WS=1``,
   ``USER_NAME=aayush``, ``NAO_SHARED_SECRET=``).
3. Wait for the local uvicorn server's ``/health`` endpoint (skippable
   via ``--no-server-check``).
4. Add ``nao/`` to ``sys.path`` so its bare-imports (``import config``,
   ``from logger import …``) resolve without a package rewrite.
5. Spawn ``nao.main.main()`` on a daemon thread.
6. Open the Mac mic with ``MicCapture`` and forward 20 ms PCM frames into
   ``FakeALAudioDevice.processRemote`` (which delivers them to the
   ALModule subscriber pattern that ``audio_module.NaoAudioStreamer``
   expects on the robot).
7. Hook ``FakeALAudioPlayer.playFile(path)`` to read the file and feed
   ``SpeakerOut.play()`` so server TTS plays through the Mac speaker.
8. Read raw stdin for hotkeys: ``f`` injects a face detection event,
   ``t`` injects a head touch (barge-in), ``b`` flips a barge-in flag,
   ``q`` exits cleanly.
9. Render LED state changes via ``LedsConsoleRenderer`` from the sibling
   ``fake-naoqi-mod`` agent.
10. Append per-turn timing rows to ``~/nao_assist/sim_latency.csv``.

The driver is robust: missing audio devices, missing scenarios, or
non-merged sibling modules surface as ``[live_nao]`` warnings rather
than crashes. The ``--dry-run`` mode does not open audio devices, does
not import ``nao.main``, and exits cleanly — perfect smoke test for CI.

Compatibility
-------------
* Python 3.11+ (uses ``typing`` features from PEP 604 / PEP 695-ish
  syntax conservatively; tested under stock 3.11 and 3.12).
* Optional deps: ``sounddevice``, ``numpy``, ``pydub``, ``requests``.
  All are guarded — missing deps give degraded-mode warnings.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import io
import logging
import os
import select
import sys
import termios
import threading
import time
import traceback
import tty
from pathlib import Path
from typing import Any, Callable, Optional


# When invoked as ``python sim/live_nao.py``, sys.path[0] is sim/ itself,
# which means ``from sim.audio_io import ...`` fails with ModuleNotFoundError.
# Insert the repo root so the ``sim.`` package resolves whether we're
# launched from the repo root, from inside sim/, or via ``python -m``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


logger = logging.getLogger("sim.live_nao")


# ── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_USER_NAME = "aayush"
DEFAULT_FACE_ID = "aayush"
DEFAULT_LATENCY_CSV = "~/nao_assist/sim_latency.csv"
DEFAULT_HEALTH_TIMEOUT_S = 30.0
DEFAULT_HEALTH_POLL_S = 0.5

# WS_PORT defaults to whatever the operator's uvicorn lands on. Server
# config defaults SERVER_PORT to 5050 (see nao/config.py); we mirror that
# but allow override via --port / WS_PORT env.
DEFAULT_WS_PORT = int(os.environ.get("WS_PORT") or os.environ.get("SERVER_PORT") or "5050")


# ── Hotkey events ───────────────────────────────────────────────────────────
HOTKEY_FACE = "f"
HOTKEY_TOUCH = "t"
HOTKEY_BARGE = "b"
HOTKEY_QUIT = "q"


# ── Paths ───────────────────────────────────────────────────────────────────
def _expand(p: str) -> Path:
    return Path(os.path.expanduser(p))


def _ensure_parent_dir(p: Path) -> None:
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not create parent dir %s: %s", p.parent, exc)


# ── Latency telemetry CSV ──────────────────────────────────────────────────
class LatencyCsv:
    """Tiny appender for per-turn timings.

    Columns:
        ts_iso, turn_idx, user_text, reply_preview,
        e2e_user_to_first_audio_ms, e2e_user_to_complete_ms, outcome.
    """

    HEADER = [
        "ts_iso",
        "turn_idx",
        "user_text",
        "reply_preview",
        "e2e_user_to_first_audio_ms",
        "e2e_user_to_complete_ms",
        "outcome",
    ]

    def __init__(self, path: str = DEFAULT_LATENCY_CSV) -> None:
        self.path = _expand(path)
        _ensure_parent_dir(self.path)
        self._lock = threading.Lock()
        self._ensure_header()

    def _ensure_header(self) -> None:
        try:
            need_header = not self.path.exists() or self.path.stat().st_size == 0
            if need_header:
                with self.path.open("a", newline="", encoding="utf-8") as fh:
                    writer = csv.writer(fh)
                    writer.writerow(self.HEADER)
        except Exception as exc:  # noqa: BLE001
            logger.warning("latency csv header write failed: %s", exc)

    def append(
        self,
        turn_idx: int,
        user_text: str,
        reply_preview: str,
        first_audio_ms: float | None,
        complete_ms: float | None,
        outcome: str,
    ) -> None:
        ts = _dt.datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
        row = [
            ts,
            int(turn_idx),
            (user_text or "")[:200],
            (reply_preview or "")[:200],
            "" if first_audio_ms is None else "{0:.1f}".format(first_audio_ms),
            "" if complete_ms is None else "{0:.1f}".format(complete_ms),
            outcome or "",
        ]
        try:
            with self._lock:
                with self.path.open("a", newline="", encoding="utf-8") as fh:
                    csv.writer(fh).writerow(row)
        except Exception as exc:  # noqa: BLE001
            logger.warning("latency csv append failed: %s", exc)


# ── /health probe ───────────────────────────────────────────────────────────
def _wait_for_health(
    url: str,
    timeout_s: float,
    poll_s: float = DEFAULT_HEALTH_POLL_S,
) -> bool:
    """Poll ``url`` until it returns 200 or ``timeout_s`` elapses.

    Returns True on success, False on timeout. Uses ``urllib`` so we don't
    add a hard dep on ``requests``.
    """
    import urllib.error
    import urllib.request

    deadline = time.time() + float(timeout_s)
    last_exc: Optional[BaseException] = None
    print("[live_nao] waiting for {0}".format(url))
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if 200 <= resp.status < 300:
                    return True
        except urllib.error.URLError as exc:
            last_exc = exc
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        time.sleep(poll_s)
    if last_exc is not None:
        print("[live_nao] /health timeout: {0!r}".format(last_exc))
    return False


# ── Hotkey listener (raw-mode stdin) ────────────────────────────────────────
class HotkeyListener:
    """Reads single keys from stdin without echo or line-buffering.

    Avoids the ``pynput`` dependency: ``termios`` + ``select`` is on every
    POSIX. On systems where ``stdin`` isn't a TTY (CI, piped input), this
    silently no-ops so ``--scenario`` and ``--dry-run`` modes still work.
    """

    def __init__(self, on_key: Callable[[str], None]) -> None:
        self._on_key = on_key
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._old_attrs: Any = None
        self._fd: int = -1
        self._tty_ok = False

    def start(self) -> None:
        try:
            fd = sys.stdin.fileno()
        except (AttributeError, io.UnsupportedOperation):
            logger.info("hotkeys disabled: stdin has no fileno()")
            return
        if not os.isatty(fd):
            logger.info("hotkeys disabled: stdin is not a TTY")
            return
        try:
            self._old_attrs = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            self._fd = fd
            self._tty_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("hotkeys: failed to enter cbreak mode: %s", exc)
            return

        self._thread = threading.Thread(
            target=self._loop, name="live_nao-hotkeys", daemon=True,
        )
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                rlist, _, _ = select.select([self._fd], [], [], 0.2)
            except Exception:
                # stdin closed or interrupted — bail.
                break
            if not rlist:
                continue
            try:
                ch = os.read(self._fd, 1).decode(errors="ignore")
            except Exception:
                break
            if not ch:
                continue
            try:
                self._on_key(ch)
            except Exception as exc:  # noqa: BLE001
                logger.warning("hotkey callback failed: %s", exc)

    def stop(self) -> None:
        self._stop.set()
        if self._tty_ok and self._old_attrs is not None and self._fd >= 0:
            try:
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_attrs)
            except Exception:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.5)


# ── Fake-naoqi shim wiring ─────────────────────────────────────────────────
def _install_fakes(echo_sim: bool, on_event: Optional[Callable] = None):
    """Install ``sim.fake_naoqi`` into ``sys.modules``.

    Returns a tuple ``(fake_module, leds_renderer, echo_simulator)``.
    Items may be ``None`` when the sibling ``fake-naoqi-mod`` agent has
    not landed yet — the rest of the driver continues with logged
    warnings rather than crashing, so this module is exercisable in
    isolation.
    """
    fake_mod = None
    leds_renderer = None
    echo_simulator = None

    try:
        from sim import fake_naoqi as fake_mod  # type: ignore  # noqa: F811
    except Exception as exc:  # noqa: BLE001
        print(
            "[live_nao] sim.fake_naoqi not importable yet ({0}); "
            "continuing without naoqi fakes".format(exc)
        )

    try:
        from sim.leds_console import LedsConsoleRenderer  # type: ignore
        leds_renderer = LedsConsoleRenderer()
    except Exception as exc:  # noqa: BLE001
        logger.info("leds_console not available: %s", exc)

    if echo_sim:
        try:
            from sim.echo_sim import EchoSimulator  # type: ignore
            echo_simulator = EchoSimulator()
        except Exception as exc:  # noqa: BLE001
            logger.info("echo_sim requested but unavailable: %s", exc)

    if fake_mod is not None:
        installer = getattr(fake_mod, "install_into_sys_modules", None)
        if not callable(installer):
            print(
                "[live_nao] sim.fake_naoqi.install_into_sys_modules missing; "
                "naoqi imports may still fail"
            )
        else:
            try:
                installer(
                    echo_sim=echo_simulator,
                    leds_renderer=leds_renderer,
                    on_event=on_event,
                )
                print("[live_nao] fake_naoqi installed into sys.modules")
            except Exception as exc:  # noqa: BLE001
                print(
                    "[live_nao] fake_naoqi.install_into_sys_modules failed: {0!r}".format(exc)
                )

    return fake_mod, leds_renderer, echo_simulator


# ── Mic forwarding ─────────────────────────────────────────────────────────
def _forward_mic_to_fake_audio_device(
    mic: Any,
    fake_mod: Any,
    stop_event: threading.Event,
) -> threading.Thread:
    """Pump frames from ``mic.iter_frames()`` into the fake ALAudioDevice.

    Real NAOqi calls ``module.processRemote(nbOfChannels,
    nbOfSamplesByChannel, timeStamp, inputBuffer)`` from a C++ thread.
    Our fake exposes that same shape — we look up the registered
    subscriber on ``fake_mod.FakeALAudioDevice`` and call its
    ``processRemote``.
    """

    def _run() -> None:
        if fake_mod is None or mic is None:
            return
        sample_rate = getattr(mic, "sample_rate", 16_000)
        bytes_per_frame = getattr(mic, "bytes_per_frame", 640)
        samples_per_frame = bytes_per_frame // 2  # int16 mono

        get_subscribers = getattr(fake_mod, "get_audio_subscribers", None)
        # Fall back to a known attribute name if the helper isn't there.
        if not callable(get_subscribers):
            def get_subscribers():
                fake_dev_cls = getattr(fake_mod, "FakeALAudioDevice", None)
                inst = getattr(fake_dev_cls, "_instance", None)
                if inst is None:
                    return []
                subs = getattr(inst, "subscribers", None)
                if isinstance(subs, dict):
                    return list(subs.values())
                if isinstance(subs, list):
                    return list(subs)
                return []

        for raw in mic.iter_frames():
            if stop_event.is_set():
                break
            if not raw:
                continue
            ts_ms = int(time.time() * 1000)
            try:
                subscribers = get_subscribers() or []
            except Exception as exc:  # noqa: BLE001
                logger.debug("get_subscribers failed: %s", exc)
                subscribers = []
            for module in subscribers:
                pr = getattr(module, "processRemote", None)
                if not callable(pr):
                    continue
                try:
                    pr(1, samples_per_frame, ts_ms, raw)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("processRemote raised: %s", exc)

    t = threading.Thread(target=_run, name="live_nao-mic-fwd", daemon=True)
    t.start()
    return t


# ── Speaker forwarding ─────────────────────────────────────────────────────
def _hook_audio_player(fake_mod: Any, speaker: Any) -> None:
    """Wire ``FakeALAudioPlayer.playFile`` so playback hits the Mac speaker."""
    if fake_mod is None or speaker is None:
        return
    fake_player_cls = getattr(fake_mod, "FakeALAudioPlayer", None)
    if fake_player_cls is None:
        logger.info("FakeALAudioPlayer not present yet; speaker hook skipped")
        return

    set_handler = getattr(fake_player_cls, "set_play_handler", None)
    if callable(set_handler):
        # Preferred: explicit hook from the sibling agent.
        def _handler(path: str, _post_id: int = 0) -> None:
            _play_file_through_speaker(path, speaker)
        try:
            set_handler(_handler)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("set_play_handler failed: %s", exc)

    # Fallback: monkey-patch playFile / post.playFile if needed.
    orig_play = getattr(fake_player_cls, "playFile", None)

    def _wrap_play(self, path, *args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            _play_file_through_speaker(path, speaker)
        except Exception as exc:  # noqa: BLE001
            logger.warning("speaker hook failed: %s", exc)
        if callable(orig_play):
            try:
                return orig_play(self, path, *args, **kwargs)
            except Exception:
                return None
        return 0  # the real ALAudioPlayer.playFile returns a task id

    try:
        fake_player_cls.playFile = _wrap_play  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not wrap FakeALAudioPlayer.playFile: %s", exc)


def _play_file_through_speaker(path: str, speaker: Any) -> None:
    """Read ``path`` and hand the bytes to ``speaker.play``."""
    try:
        p = Path(path)
        if not p.exists():
            logger.debug("speaker hook: file not found %s", path)
            return
        data = p.read_bytes()
    except Exception as exc:  # noqa: BLE001
        logger.warning("speaker hook read failed: %s", exc)
        return
    fmt = "wav" if path.lower().endswith(".wav") else "mp3"
    try:
        speaker.play(data, format=fmt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("speaker.play failed: %s", exc)


# ── Hotkey actions ─────────────────────────────────────────────────────────
def _resolve_method(cls_or_obj: Any, method_name: str):
    """Return a callable bound to an *instance* if at all possible.

    Sibling ``fake_naoqi`` agents may expose either a singleton class with
    ``_instance`` or directly-instantiable objects. Calling an unbound
    function from a class object will crash (missing ``self``); always
    prefer the instance-bound method if one is reachable.
    """
    if cls_or_obj is None:
        return None
    # Instance-level lookup first.
    instance = getattr(cls_or_obj, "_instance", None)
    if instance is not None:
        m = getattr(instance, method_name, None)
        if callable(m):
            return m
    # Class-level fallback. Only useful if ``cls_or_obj`` is itself an
    # instance (in which case getattr returns a bound method).
    m = getattr(cls_or_obj, method_name, None)
    if callable(m):
        # If we landed on a bare function (descriptor protocol on the
        # class returned an unbound), try to bind it to a class-level
        # default attribute. Best-effort; if no instance exists we just
        # return None so the caller falls through to its next path.
        if hasattr(m, "__self__"):
            return m  # already bound
        # Not bound — only useful if cls_or_obj is itself an instance.
        if not isinstance(cls_or_obj, type):
            return m
    return None


class HotkeyActions:
    """Bridges keystrokes to ALMemory injections + barge flags.

    Every action is wrapped in try/except — a missing fake module turns a
    keypress into a logged no-op rather than crashing the listener.
    """

    def __init__(
        self,
        fake_mod: Any,
        face_id: str = DEFAULT_FACE_ID,
        on_quit: Optional[Callable[[], None]] = None,
    ) -> None:
        self.fake_mod = fake_mod
        self.face_id = face_id
        self.on_quit = on_quit
        # ``_barge_in`` flag. The wake-state-machine sibling polls
        # ALMemory for ``BargeIn`` / similar; we set the flag both as a
        # public attribute and as an ALMemory key so whichever consumer
        # is wired up will see it.
        self.barge_in = threading.Event()
        self._face_seq = 0
        self._touch_seq = 0

    def handle(self, key: str) -> None:
        k = key.lower()
        if k == HOTKEY_FACE:
            self._inject_face()
        elif k == HOTKEY_TOUCH:
            self._inject_head_touch()
        elif k == HOTKEY_BARGE:
            self._set_barge_in()
        elif k == HOTKEY_QUIT:
            if self.on_quit is not None:
                self.on_quit()
        else:
            # Unknown key — silently ignore (avoids spam on arrow keys etc.).
            pass

    # --- specific actions -------------------------------------------------
    def _inject_face(self) -> None:
        if self.fake_mod is None:
            print("[hotkey] face wake requested, but fake_naoqi missing")
            return
        self._face_seq += 1
        # ALFaceDetection on real NAOqi writes
        # ALMemory["FaceDetected"] = [TimestampField, FaceInfoField, ...].
        # Sibling fake_naoqi.py is responsible for the precise shape; we
        # just call its inject hook with the canonical face_id and a
        # synthetic confidence. Two access paths so we work with whatever
        # the sibling settles on:
        injected = False
        face_payload = {
            "face_id": self.face_id,
            "confidence": 0.9,
            "distance_m": 0.6,
            "seq": self._face_seq,
            "ts": time.time(),
        }
        face_det_cls = getattr(self.fake_mod, "FakeALFaceDetection", None)
        inject = _resolve_method(face_det_cls, "inject_face")
        if callable(inject):
            try:
                inject(self.face_id, confidence=0.9, distance_m=0.6)
                injected = True
            except TypeError:
                try:
                    inject(face_payload)
                    injected = True
                except Exception as exc:  # noqa: BLE001
                    logger.warning("inject_face failed: %s", exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("inject_face failed: %s", exc)
        if not injected:
            mem_cls = getattr(self.fake_mod, "FakeALMemory", None)
            inject = _resolve_method(mem_cls, "inject")
            if callable(inject):
                try:
                    inject("FaceDetected", face_payload)
                    injected = True
                except Exception as exc:  # noqa: BLE001
                    logger.warning("ALMemory.inject failed: %s", exc)
        if injected:
            print("[hotkey] face wake -> face_id={0}".format(self.face_id))
        else:
            print("[hotkey] face wake: no inject path on fake_naoqi")

    def _inject_head_touch(self) -> None:
        if self.fake_mod is None:
            print("[hotkey] head touch requested, but fake_naoqi missing")
            return
        self._touch_seq += 1
        injected = False
        mem_cls = getattr(self.fake_mod, "FakeALMemory", None)
        inject = _resolve_method(mem_cls, "inject")
        if callable(inject):
            # Real ALTouch event keys.
            for key in ("FrontTactilTouched", "MiddleTactilTouched"):
                try:
                    inject(key, 1.0)
                    # Auto-release: real Aldebaran fires 1 then 0 a beat
                    # later. We do the same so wake-state code that
                    # debounces on rising edges sees a clean event.
                    threading.Timer(
                        0.15, _safe_call, args=(inject, key, 0.0),
                    ).start()
                    injected = True
                except Exception as exc:  # noqa: BLE001
                    logger.warning("ALMemory.inject(%s) failed: %s", key, exc)
        if injected:
            print("[hotkey] head touch (tactile)")
        else:
            print("[hotkey] head touch: no inject path on fake_naoqi")

    def _set_barge_in(self) -> None:
        self.barge_in.set()
        injected = False
        if self.fake_mod is not None:
            mem_cls = getattr(self.fake_mod, "FakeALMemory", None)
            inject = _resolve_method(mem_cls, "inject")
            if callable(inject):
                try:
                    inject("BargeIn", 1.0)
                    threading.Timer(
                        0.20, _safe_call, args=(inject, "BargeIn", 0.0),
                    ).start()
                    injected = True
                except Exception as exc:  # noqa: BLE001
                    logger.warning("ALMemory.inject(BargeIn) failed: %s", exc)
        if injected:
            print("[hotkey] barge-in flag asserted via ALMemory")
        else:
            print("[hotkey] barge-in flag set (no fake_naoqi memory hook)")


def _safe_call(fn: Callable[..., Any], *args: Any) -> None:
    try:
        fn(*args)
    except Exception:
        pass


# ── Boot orchestration ─────────────────────────────────────────────────────
def _start_nao_main_thread(stop_event: threading.Event) -> Optional[threading.Thread]:
    """Import ``nao.main`` and run its ``main()`` on a daemon thread.

    The robot-side ``nao/main.py`` uses bare imports (``import config``,
    ``from utils import …``) because it's designed to be launched with
    ``cwd=/home/nao/nao_assist``. We replicate that by adding ``nao/``
    to ``sys.path`` *first*, then importing ``main`` as a top-level
    module under that path.
    """
    repo_root = Path(__file__).resolve().parent.parent
    nao_dir = repo_root / "nao"
    if not nao_dir.is_dir():
        print("[live_nao] nao/ directory missing — cannot start nao.main")
        return None

    nao_path = str(nao_dir)
    if nao_path not in sys.path:
        sys.path.insert(0, nao_path)

    try:
        import main as nao_main  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        print("[live_nao] failed to import nao.main: {0!r}".format(exc))
        traceback.print_exc()
        return None

    main_fn = getattr(nao_main, "main", None)
    if not callable(main_fn):
        print("[live_nao] nao.main has no callable main()")
        return None

    def _runner() -> None:
        try:
            main_fn()
        except KeyboardInterrupt:
            pass
        except Exception as exc:  # noqa: BLE001
            print("[live_nao] nao.main crashed: {0!r}".format(exc))
            traceback.print_exc()

    t = threading.Thread(target=_runner, name="live_nao-nao-main", daemon=True)
    t.start()
    return t


# ── Banner / args ──────────────────────────────────────────────────────────
def _print_banner(mic_name: str, speaker_name: str) -> None:
    print()
    print("=== Virtual NAO ===")
    print("mic: {0} @ 16 kHz".format(mic_name))
    print("speaker: {0}".format(speaker_name))
    print("fake face_id: {0}".format(DEFAULT_FACE_ID))
    print("hotkeys: [f] face wake  [t] head touch  [b] barge-in  [q] quit")
    print("==================")
    print()


def _resolve_device_names() -> tuple[str, str]:
    """Return ``(mic_name, speaker_name)`` for the banner.

    Uses ``sounddevice.query_devices()`` if available; otherwise prints a
    placeholder so the banner always succeeds.
    """
    try:
        import sounddevice as sd  # type: ignore
        try:
            in_idx, out_idx = sd.default.device  # type: ignore[attr-defined]
        except Exception:
            in_idx, out_idx = (None, None)
        try:
            devices = sd.query_devices()  # type: ignore
        except Exception:
            return ("default", "default")
        mic = devices[in_idx]["name"] if isinstance(in_idx, int) else "default"
        spk = devices[out_idx]["name"] if isinstance(out_idx, int) else "default"
        return (mic, spk)
    except Exception:
        return ("default (sounddevice unavailable)",
                "default (afplay/ffplay or sounddevice)")


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="live_nao.py",
        description=(
            "Virtual NAO — drives the full mic→WS→server→TTS→speaker "
            "pipeline from a Mac, no robot required."
        ),
    )
    p.add_argument(
        "--echo",
        action="store_true",
        help="Enable EchoSimulator (mixes a fraction of TTS audio back "
             "into the next mic frames so the server-side echo guard can "
             "be exercised). Off by default.",
    )
    p.add_argument(
        "--scenario",
        metavar="NAME",
        default=None,
        help="Run a single scenario from sim/scenarios/<NAME>.py instead "
             "of the interactive loop.",
    )
    p.add_argument(
        "--port",
        type=int,
        default=DEFAULT_WS_PORT,
        help=("Local server port to wait for /health on. "
              "Defaults to $WS_PORT, $SERVER_PORT, or {0}.".format(DEFAULT_WS_PORT)),
    )
    p.add_argument(
        "--no-server-check",
        action="store_true",
        help="Skip the /health probe. Use when the server isn't running "
             "and you just want a quick smoke test.",
    )
    p.add_argument(
        "--user",
        default=os.environ.get("USER_NAME", DEFAULT_USER_NAME),
        help="Username for the WS handshake (sets USER_NAME env). "
             "Defaults to 'aayush'.",
    )
    p.add_argument(
        "--latency-csv",
        default=DEFAULT_LATENCY_CSV,
        help="Where to append per-turn latency rows. "
             "Defaults to {0}.".format(DEFAULT_LATENCY_CSV),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Exercise the import + setup path WITHOUT opening real audio "
             "devices, importing nao.main, or installing fakes. Prints "
             "what each step would do and exits. Use this as the smoke test.",
    )
    p.add_argument(
        "--health-timeout",
        type=float,
        default=DEFAULT_HEALTH_TIMEOUT_S,
        help="Seconds to wait for /health before bailing.",
    )
    p.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        help="Python logging level. Default INFO.",
    )
    return p


# ── Main entry ─────────────────────────────────────────────────────────────
def _set_robot_env(user: str, port: int) -> None:
    """Push the env vars the nao-side modules read at import time."""
    os.environ.setdefault("NAO_IP", "127.0.0.1")
    os.environ.setdefault("SERVER_IP", "127.0.0.1")
    os.environ.setdefault("USE_WS", "1")
    os.environ["USER_NAME"] = user or DEFAULT_USER_NAME
    # Empty secret = OPEN mode. Live sim isn't security-critical and the
    # server will warn at boot.
    os.environ.setdefault("NAO_SHARED_SECRET", "")
    os.environ.setdefault("WS_PORT", str(port))
    # Server respects SERVER_PORT for HTTP/health URLs.
    os.environ.setdefault("SERVER_PORT", str(port))


def _run_scenario(name: str, latency: LatencyCsv, fake_mod: Any) -> int:
    """Import sim/scenarios/<name>.py and call its ``run`` function."""
    try:
        import importlib
        mod = importlib.import_module("sim.scenarios.{0}".format(name))
    except Exception as exc:  # noqa: BLE001
        print("[live_nao] scenario {0!r} not importable: {1}".format(name, exc))
        return 2
    run_fn = getattr(mod, "run", None)
    if not callable(run_fn):
        print("[live_nao] scenario {0!r} has no run() function".format(name))
        return 2
    try:
        # Scenarios accept (client, telemetry); we hand them the fake
        # module + the LatencyCsv so they can append rows. Any signature
        # mismatch surfaces as a TypeError we log and exit on.
        result = run_fn(fake_mod, latency)
        print("[live_nao] scenario {0!r} → {1!r}".format(name, result))
        return 0
    except Exception as exc:  # noqa: BLE001
        print("[live_nao] scenario {0!r} crashed: {1!r}".format(name, exc))
        traceback.print_exc()
        return 3


def _print_dry_run_plan(args: argparse.Namespace) -> None:
    """Walk the boot sequence without touching audio devices."""
    print("[dry-run] would install fake_naoqi (echo_sim={0})".format(args.echo))
    print("[dry-run] would set env: NAO_IP=127.0.0.1, SERVER_IP=127.0.0.1, "
          "USE_WS=1, USER_NAME={0}, WS_PORT={1}".format(args.user, args.port))
    if args.no_server_check:
        print("[dry-run] would skip /health probe")
    else:
        print("[dry-run] would wait for http://127.0.0.1:{0}/health".format(args.port))
    print("[dry-run] would start mic (16 kHz mono PCM16, 20 ms frames)")
    print("[dry-run] would boot ws_client via nao.main on a daemon thread")
    print("[dry-run] would forward FakeALAudioPlayer.playFile -> SpeakerOut")
    print("[dry-run] would listen for hotkeys: [f] face wake  [t] head touch  "
          "[b] barge-in  [q] quit")
    print("[dry-run] would append latency rows to {0}".format(
        _expand(args.latency_csv)))
    # Show which optional deps are present so it's obvious what's degraded.
    try:
        from sim.audio_io import degraded_reason
        reason = degraded_reason()
    except Exception as exc:  # noqa: BLE001
        reason = "audio_io import failed: {0!r}".format(exc)
    if reason:
        print("[dry-run] note: {0}".format(reason))
    print("[dry-run] OK")


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="[%(name)s] %(levelname)s %(message)s",
    )

    if args.dry_run:
        _print_dry_run_plan(args)
        return 0

    # Push env early so nao/config.py picks the right values at import.
    _set_robot_env(args.user, args.port)

    latency = LatencyCsv(args.latency_csv)

    # 1. Install fakes (with on_event hook → latency CSV via scenarios).
    # ``leds_renderer`` and ``_echo_sim`` are kept by ``fake_naoqi`` once
    # injected into ``sys.modules`` — we just need them returned so they
    # don't get garbage-collected here. Held in locals for clarity.
    fake_mod, _leds_renderer, _echo_sim = _install_fakes(
        echo_sim=args.echo, on_event=None,
    )

    # 2. Health probe.
    if not args.no_server_check:
        url = "http://127.0.0.1:{0}/health".format(args.port)
        if not _wait_for_health(url, args.health_timeout):
            print(
                "[live_nao] server not responding on :{0}; "
                "use --no-server-check to skip this gate. exiting".format(args.port)
            )
            return 4

    # 3. Open audio devices.
    try:
        from sim.audio_io import MicCapture, SpeakerOut, degraded_reason
    except Exception as exc:  # noqa: BLE001
        print("[live_nao] sim.audio_io import failed: {0!r}".format(exc))
        return 5
    deg = degraded_reason()
    if deg:
        print("[live_nao] {0}".format(deg))

    mic = MicCapture()
    speaker = SpeakerOut()
    mic.start()

    mic_name, speaker_name = _resolve_device_names()
    _print_banner(mic_name, speaker_name)

    # 4. Speaker hook on the fake audio player.
    _hook_audio_player(fake_mod, speaker)

    # 5. Mic forwarding into FakeALAudioDevice subscribers. Thread is
    # daemon, so we don't need to retain a handle to join it on exit —
    # ``stop_event`` is what tells the worker to drain and quit.
    stop_event = threading.Event()
    _forward_mic_to_fake_audio_device(mic, fake_mod, stop_event)

    # 6. Run scenario or interactive.
    if args.scenario:
        # Scenarios get a clean turn; we don't start nao.main for them.
        rc = _run_scenario(args.scenario, latency, fake_mod)
        stop_event.set()
        try:
            mic.stop()
        except Exception:
            pass
        try:
            speaker.stop()
        except Exception:
            pass
        return rc

    # 7. Boot nao.main on a daemon thread.
    nao_thread = _start_nao_main_thread(stop_event)
    if nao_thread is None:
        # Without nao.main we still keep the loop up so the operator can
        # kick scenarios manually via hotkeys; warn loudly.
        print("[live_nao] running without nao.main — hotkeys still active")

    # 8. Hotkey listener.
    quit_flag = threading.Event()

    def _on_quit() -> None:
        print("[live_nao] quit requested")
        quit_flag.set()

    actions = HotkeyActions(fake_mod, on_quit=_on_quit)
    keys = HotkeyListener(actions.handle)
    keys.start()

    # 9. Block until Ctrl-C / q.
    try:
        while not quit_flag.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n[live_nao] keyboard interrupt")
    finally:
        # Clean shutdown order matters: stop hotkeys first so the
        # terminal is restored before we print further status.
        keys.stop()
        stop_event.set()
        try:
            mic.stop()
        except Exception:
            pass
        try:
            speaker.stop()
        except Exception:
            pass
        # If we have a wake-state machine running on the nao.main thread
        # we'd like to ask it to stop, but the public API isn't reachable
        # from here (it lives inside nao.main's local frame). The thread
        # is daemon so it'll die with the process. Best we can do is wait
        # briefly for graceful exit.
        if nao_thread is not None and nao_thread.is_alive():
            nao_thread.join(timeout=1.0)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
