"""Pytest fixtures for the Phase 10.5 virtual-robot simulator.

Owned by the `e2e-test` slug. Lives under `sim/` so it does not collide
with `server/tests/conftest.py` (owned by other phases).

The fixtures are also imported as a plugin from
``server/tests/test_virtual_robot_e2e.py`` via ``pytest_plugins =
("sim.conftest",)`` so the e2e tests under ``server/tests/`` can reuse
them. Loading via ``pytest_plugins`` requires ``sim/__init__.py`` to
exist; that file is owned by the parallel ``fake-naoqi-mod`` slug. Until
that lands the e2e test pre-flights with ``pytest.importorskip`` so
collection still succeeds.

Fixtures
--------
- ``installed_fakes``  (session) — calls ``fake_naoqi.install_into_sys_modules()``.
- ``boot_ws_server``   (session) — starts uvicorn ``server.app_ws:app`` on a
  free port, polls ``/health``, returns a small handle with ``.url``,
  ``.port``, ``.shutdown()`` (auto-shutdown on session teardown).
- ``mocked_openai``    (function) — monkeypatches the OpenAI client's
  ``chat.completions``, ``audio.speech``, and ``audio.transcriptions``
  surfaces with deterministic canned responses. Activated when the env
  ``OPENAI_API_KEY`` is unset, empty, or literally ``"fake"``.
- ``mocked_cs_navigator`` (function) — monkeypatches ``httpx.AsyncClient``
  for the cs_navigator tool's outbound calls so no real network ever
  happens. Returns a canned blurb keyed off the query string.
- ``telemetry``        (function) — returns a per-test ``Telemetry``
  instance writing to ``tmp_path/sim_latency.csv``. Falls back to a thin
  stand-in when ``sim.telemetry`` hasn't merged yet so e2e tests still
  exercise the contract.

All fixtures are defensive against missing siblings (sim isn't merged,
sounddevice isn't installed, etc.). A scenario raising never kills the
session — the e2e test sees a single failure for the offending row.
"""
from __future__ import annotations

import csv
import importlib
import os
import socket
import statistics
import sys
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# 1) installed_fakes  — session-scoped. Idempotent; safe to call once per
#    test session. Calls ``sim.fake_naoqi.install_into_sys_modules()``
#    if the module exists, otherwise yields ``None`` so dependent fixtures
#    can opt out cleanly.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def installed_fakes() -> Iterator[Any]:
    """Install the fake naoqi/qi shims into ``sys.modules`` for the session.

    Owned by the ``fake-naoqi-mod`` slug. We import lazily and tolerate
    the module not existing yet — when the parent module isn't there, we
    yield ``None`` so tests parametrized over scenarios still report a
    clean ``skip`` rather than a hard collection failure.
    """
    try:
        from sim import fake_naoqi  # type: ignore[import-not-found]
    except Exception:
        yield None
        return

    install = getattr(fake_naoqi, "install_into_sys_modules", None)
    if not callable(install):
        yield None
        return

    # Idempotent install. The contract from the task map says callers can
    # pass echo_sim / leds_renderer / on_event; for the e2e harness we use
    # the default no-op renderers.
    with suppress(Exception):
        install()

    try:
        yield fake_naoqi
    finally:
        # Don't aggressively unwind sys.modules — the session is about to
        # exit anyway and pulling out the fakes mid-suite could break any
        # nao/* imports cached by other modules.
        pass


# ─────────────────────────────────────────────────────────────────────────────
# 2) boot_ws_server  — session-scoped uvicorn instance running
#    ``server.app_ws:app`` on a free port. Returns a small immutable handle.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class WsServerHandle:
    """Returned by ``boot_ws_server``.

    Attributes
    ----------
    host : str
        Bound interface (always ``127.0.0.1`` for tests).
    port : int
        Concrete TCP port. Different per test session.
    url : str
        ``http://{host}:{port}`` — convenience for HTTP probes.
    ws_url : str
        ``ws://{host}:{port}`` — convenience for WebSocket clients.
    shutdown : Callable[[], None]
        Idempotent stop-and-join. Called automatically in the fixture
        teardown but exposed so scenarios can shut the server down early
        when they need to assert post-shutdown invariants.
    """
    host: str
    port: int
    url: str
    ws_url: str
    shutdown: Callable[[], None] = field(repr=False)


def _free_tcp_port() -> int:
    """Bind to port 0 to grab an ephemeral free port, then release it.

    There's an unavoidable TOCTOU window here, but it's nano-scale —
    uvicorn binds within tens of ms. We accept the race because the
    alternative (passing a pre-bound socket into uvicorn) is fragile
    across uvicorn versions.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return sock.getsockname()[1]


def _wait_for_health(url: str, timeout_s: float = 10.0) -> bool:
    """Poll ``GET /health`` until it returns 200 or the timeout expires.

    Uses ``urllib`` from the stdlib so the check works even if ``httpx``
    isn't importable (e.g., a degraded CI image without our dev deps).
    """
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + "/health", timeout=0.5) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, OSError):
            time.sleep(0.05)
        except Exception:
            time.sleep(0.05)
    return False


@pytest.fixture(scope="session")
def boot_ws_server() -> Iterator[WsServerHandle]:
    """Boot ``server.app_ws:app`` on a free port via uvicorn in a thread.

    Skips the dependent test if either uvicorn or ``server.app_ws`` is
    unavailable in this worktree. Tear-down sets ``server.should_exit``
    and joins the thread with a 5s budget. The thread is daemon so a
    runaway uvicorn never blocks pytest exit even if the join overruns.
    """
    pytest.importorskip("uvicorn")
    pytest.importorskip("server.app_ws")

    import uvicorn  # noqa: WPS433  (lazy: see importorskip above)
    from server import app_ws  # noqa: WPS433

    host = "127.0.0.1"
    port = _free_tcp_port()
    url = f"http://{host}:{port}"
    ws_url = f"ws://{host}:{port}"

    # ``loop="asyncio"`` keeps us off uvloop, which doesn't always coexist
    # with pytest's anyio plugin on macOS. ``log_level="warning"`` cuts
    # the boot chatter so the test output stays readable.
    cfg = uvicorn.Config(
        app=app_ws.app,
        host=host,
        port=port,
        log_level="warning",
        loop="asyncio",
        lifespan="on",
        access_log=False,
    )
    server = uvicorn.Server(cfg)

    # uvicorn installs signal handlers when ``Server.run()`` is called from
    # the main thread. We're on a worker thread, so disable that path —
    # ``install_signal_handlers`` is a method, override it on the instance.
    server.install_signal_handlers = lambda: None  # type: ignore[assignment]

    def _serve() -> None:
        # Run in its own asyncio loop on this thread.
        try:
            server.run()
        except Exception:
            # Fail-soft: log and exit. The /health probe below will time
            # out and the dependent tests will see a clean fixture error.
            pass

    thread = threading.Thread(target=_serve, name="uvicorn-app_ws", daemon=True)
    thread.start()

    if not _wait_for_health(url, timeout_s=10.0):
        # Best-effort shutdown on probe failure so we don't leak a thread
        # to the next test session.
        server.should_exit = True
        thread.join(timeout=2.0)
        pytest.fail(
            f"app_ws health probe at {url}/health did not return 200 within 10s",
            pytrace=False,
        )

    shutdown_called = {"done": False}

    def _shutdown() -> None:
        if shutdown_called["done"]:
            return
        shutdown_called["done"] = True
        server.should_exit = True
        thread.join(timeout=5.0)

    handle = WsServerHandle(
        host=host, port=port, url=url, ws_url=ws_url, shutdown=_shutdown,
    )

    try:
        yield handle
    finally:
        _shutdown()


# ─────────────────────────────────────────────────────────────────────────────
# 3) mocked_openai  — function-scoped. Patches the OpenAI client classes so
#    no real network call is ever made when ``OPENAI_API_KEY`` is unset,
#    empty, or literally ``"fake"``. When a real key is present we return
#    a no-op marker so the caller knows we did NOT patch anything.
# ─────────────────────────────────────────────────────────────────────────────


# Canned text returned by chat completions. Short and on-topic enough that
# the agent doesn't decide to retry — we don't want flapping in CI.
_CANNED_REPLY = "Hi! I'm here. (mocked)"

# 1 KB of MP3-shaped bytes — long enough that downstream code that
# fingerprints the format succeeds, short enough to keep test memory tiny.
_CANNED_MP3 = b"\xff\xfb\x90\x00" + b"\x00" * 1020


@dataclass
class _CannedChatChoice:
    finish_reason: str = "stop"
    index: int = 0

    @property
    def message(self) -> Any:
        # The real openai-python returns a Pydantic ``ChatCompletionMessage``
        # with a ``content`` attr. Provide an attribute-bag stand-in.
        return type("M", (), {"role": "assistant", "content": _CANNED_REPLY})()


@dataclass
class _CannedChatCompletion:
    id: str = "chatcmpl-mock"
    model: str = "gpt-4o-mock"
    object: str = "chat.completion"
    choices: list = field(default_factory=lambda: [_CannedChatChoice()])
    usage: Any = field(default_factory=lambda: type("U", (), {
        "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
    })())


@dataclass
class _CannedTranscription:
    text: str = "hello"


class _CannedSpeechResponse:
    """Mimics ``openai.audio.speech.create(...)`` return.

    The real return is a streaming response with ``.read()`` /
    ``.iter_bytes()``. Tests that go through ``server.openai_tts``
    actually pull bytes via ``response.content``. We expose all three
    surfaces so we don't lock the test against one specific access path.
    """

    def __init__(self, payload: bytes = _CANNED_MP3):
        self._payload = payload
        self.content = payload

    def read(self) -> bytes:
        return self._payload

    def iter_bytes(self, _chunk_size: int = 4096):
        yield self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _MockChatCompletions:
    def create(self, **_kwargs: Any) -> _CannedChatCompletion:
        return _CannedChatCompletion()


class _MockChat:
    def __init__(self) -> None:
        self.completions = _MockChatCompletions()


class _MockSpeech:
    def create(self, **_kwargs: Any) -> _CannedSpeechResponse:
        return _CannedSpeechResponse()

    def with_streaming_response(self):
        # Some openai-python versions expose ``client.audio.speech
        # .with_streaming_response.create(...)``. Return the same canned
        # bytes regardless of which API tier is used.
        return self


class _MockTranscriptions:
    def create(self, **_kwargs: Any) -> _CannedTranscription:
        return _CannedTranscription()


class _MockAudio:
    def __init__(self) -> None:
        self.speech = _MockSpeech()
        self.transcriptions = _MockTranscriptions()


class _MockOpenAIClient:
    """Stand-in for ``openai.OpenAI`` covering the surfaces this codebase
    actually calls: ``client.chat.completions.create``,
    ``client.audio.transcriptions.create``, ``client.audio.speech.create``.

    Instances are cheap; constructed fresh whenever the patched factory is
    invoked. Anything the codebase doesn't touch raises ``AttributeError``
    on access — that's deliberate; we want to know if a new call site
    starts expecting a surface we haven't whitelisted.
    """

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.chat = _MockChat()
        self.audio = _MockAudio()


def _openai_should_be_mocked() -> bool:
    """Activate the mock when no real key exists. Empty / ``"fake"`` /
    missing all count as ``no real key``. Defensive on whitespace too.
    """
    key = (os.environ.get("OPENAI_API_KEY") or "").strip().lower()
    return key in {"", "fake", "test", "sk-test"}


@pytest.fixture
def mocked_openai(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Patch the OpenAI SDK and re-exports across the server.

    Returns the mock client class so individual tests can inspect or
    extend it. When a real key is present, returns ``None`` and applies
    no patches — that lets devs run the e2e suite against the live API
    by setting ``OPENAI_API_KEY`` properly.
    """
    if not _openai_should_be_mocked():
        yield None
        return

    # 1. Patch the constructor in the openai SDK itself so any new
    #    ``OpenAI(...)`` call after the patch lands gets our mock.
    try:
        import openai
        monkeypatch.setattr(openai, "OpenAI", _MockOpenAIClient, raising=False)
    except Exception:
        pass

    # 2. Patch the bound ``_client`` instances already created at import
    #    time in modules that constructed one eagerly. Each importing
    #    module captured the real class; we replace the live binding.
    for module_path in (
        "server.server",
        "server.openai_tts",
        "server.app_ws",
    ):
        try:
            mod = importlib.import_module(module_path)
        except Exception:
            continue
        if hasattr(mod, "_client"):
            monkeypatch.setattr(mod, "_client", _MockOpenAIClient(), raising=False)
        if hasattr(mod, "OpenAI"):
            monkeypatch.setattr(mod, "OpenAI", _MockOpenAIClient, raising=False)

    # 3. Patch the ``synthesize`` shortcut used everywhere downstream so
    #    we don't depend on the streaming-response shape for TTS tests.
    try:
        from server import openai_tts as _otts

        def _fake_synth(text: str) -> bytes | None:
            if not text or not str(text).strip():
                return None
            return _CANNED_MP3

        monkeypatch.setattr(_otts, "synthesize", _fake_synth, raising=False)
        # Re-binding sites that did ``from server.openai_tts import synthesize``.
        for site in ("server.app_ws", "server.server"):
            with suppress(Exception):
                site_mod = importlib.import_module(site)
                if hasattr(site_mod, "synthesize"):
                    monkeypatch.setattr(site_mod, "synthesize", _fake_synth, raising=False)
    except Exception:
        pass

    # 4. Patch the canonical ``_transcribe`` helper so any STT path takes
    #    the mock route. Both the legacy Flask app and the new WS app
    #    expose this name.
    def _fake_transcribe(_path: str) -> str:
        return "hello"

    for site in ("server.server", "server.app_ws", "server._legacy_helpers"):
        with suppress(Exception):
            site_mod = importlib.import_module(site)
            for attr in ("_transcribe", "transcribe"):
                if hasattr(site_mod, attr):
                    monkeypatch.setattr(site_mod, attr, _fake_transcribe, raising=False)

    yield _MockOpenAIClient


# ─────────────────────────────────────────────────────────────────────────────
# 4) mocked_cs_navigator  — patches ``httpx.AsyncClient`` so the
#    ``cs_navigator_search`` tool returns a canned blurb without ever
#    hitting the real CS Navigator endpoint.
# ─────────────────────────────────────────────────────────────────────────────


_CANNED_NAVIGATOR_BODY = (
    "CS 491 is the Senior Capstone course at Morgan State University. "
    "Students design and ship a complete software project over the semester."
)


class _CannedHttpxResponse:
    """Mirrors the bits of ``httpx.Response`` that ``cs_navigator`` reads."""

    def __init__(self, status_code: int = 200,
                 json_payload: dict | None = None,
                 text: str | None = None) -> None:
        self.status_code = status_code
        self._json = json_payload if json_payload is not None else {
            "response": _CANNED_NAVIGATOR_BODY,
        }
        self.text = text if text is not None else _CANNED_NAVIGATOR_BODY
        self.headers = {"content-type": "application/json"}

    def json(self) -> dict:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    async def aiter_lines(self):
        # SSE-style iteration. The cs_navigator code that opens an SSE
        # stream reads ``data: {"response": "..."}`` lines.
        import json as _json
        yield "data: " + _json.dumps({"response": _CANNED_NAVIGATOR_BODY})
        yield "data: [DONE]"

    async def aclose(self) -> None:
        return None


class _CannedHttpxAsyncClient:
    """Drop-in for ``httpx.AsyncClient`` — only the methods we need."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self._closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._closed = True
        return False

    async def post(self, _url: str, **_kwargs: Any) -> _CannedHttpxResponse:
        return _CannedHttpxResponse()

    async def get(self, _url: str, **_kwargs: Any) -> _CannedHttpxResponse:
        return _CannedHttpxResponse()

    async def stream(self, _method: str, _url: str, **_kwargs: Any):
        # Async context manager that yields a response with ``aiter_lines``.
        class _StreamCtx:
            async def __aenter__(self_inner):
                return _CannedHttpxResponse()

            async def __aexit__(self_inner, *_a):
                return False

        return _StreamCtx()

    async def aclose(self) -> None:
        self._closed = True


@pytest.fixture
def mocked_cs_navigator(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Patch ``httpx.AsyncClient`` so ``cs_navigator`` never hits the wire.

    We patch at the ``httpx`` module level AND at the ``server.tools.
    cs_navigator`` re-export site to defeat both ``httpx.AsyncClient(...)``
    and any ``from httpx import AsyncClient`` style imports the tool
    might use across versions.
    """
    try:
        import httpx
    except Exception:
        # No httpx installed -> nothing to patch; return None so the
        # dependent test still runs (it'll skip if the tool is needed).
        yield None
        return

    monkeypatch.setattr(httpx, "AsyncClient", _CannedHttpxAsyncClient, raising=False)

    with suppress(Exception):
        from server.tools import cs_navigator as _csn  # type: ignore[import-not-found]
        if hasattr(_csn, "httpx"):
            # Replace the `httpx` reference inside the tool module with
            # an object whose ``AsyncClient`` is our canned class. The
            # tool does ``import httpx`` at module top, then later
            # ``httpx.AsyncClient(...)``; setting the attribute on the
            # module wins because Python re-resolves it at call time.
            class _Shim:
                AsyncClient = _CannedHttpxAsyncClient
                # Re-export real exception types so ``except httpx.X``
                # blocks in the tool module still work.
                TimeoutException = httpx.TimeoutException
                RequestError = httpx.RequestError
                Response = httpx.Response
                Request = httpx.Request

            monkeypatch.setattr(_csn, "httpx", _Shim, raising=False)
        # If the tool exposes its own ``_cs_navigator_search_impl`` we can
        # also patch the higher-level entry point so a missing httpx
        # surface still produces the canned blurb. Best-effort.
        if hasattr(_csn, "_cs_navigator_search_impl"):
            async def _fake_impl(_ctx: Any, _query: str) -> str:
                return _CANNED_NAVIGATOR_BODY
            monkeypatch.setattr(
                _csn, "_cs_navigator_search_impl", _fake_impl, raising=False,
            )

    yield _CannedHttpxAsyncClient


# ─────────────────────────────────────────────────────────────────────────────
# 5) telemetry  — fresh CSV per test in tmp_path. Wraps ``sim.telemetry
#    .Telemetry`` if it's available, else provides a thin stand-in with the
#    same ``mark`` / ``end_turn`` / ``percentile_ms`` surface so the e2e
#    test can keep its assertions stable even before the sibling lands.
# ─────────────────────────────────────────────────────────────────────────────


class _StubTelemetry:
    """Minimal telemetry stand-in used when ``sim.telemetry`` isn't merged.

    Implements the slice of the API the e2e test relies on:
      - ``start_turn(turn_idx)`` / ``end_turn(outcome)``
      - ``mark(phase, ms)``
      - ``percentile_ms(phase, p)``
      - ``report()``  (stringified summary)

    Writes one CSV row per ``mark`` so ``--keep-csv`` style debugging is
    still possible. ``out_csv`` is created on first write to avoid empty
    files on tests that didn't measure anything.
    """

    def __init__(self, out_csv: str | os.PathLike[str]) -> None:
        self.out_csv = Path(out_csv)
        self._marks: dict[str, list[float]] = {}
        self._turn_idx: int = -1
        self._outcomes: list[str] = []

    # --- contract surface -------------------------------------------------

    def start_turn(self, turn_idx: int) -> None:
        self._turn_idx = int(turn_idx)

    def mark(self, phase: str, ms: float) -> None:
        try:
            ms_f = float(ms)
        except (TypeError, ValueError):
            return
        self._marks.setdefault(phase, []).append(ms_f)
        self._append_row(phase, ms_f)

    def end_turn(self, outcome: str) -> None:
        self._outcomes.append(str(outcome))

    def percentile_ms(self, phase: str, p: float) -> float | None:
        samples = self._marks.get(phase) or []
        if not samples:
            return None
        # ``statistics.quantiles`` requires n>=2; for a single sample
        # fall through to that single value.
        if len(samples) == 1:
            return samples[0]
        # ``percentile`` on Python 3.13+ via statistics.quantiles is the
        # cleanest portable form.
        sorted_s = sorted(samples)
        # Linear interpolation à la NumPy's default ``linear`` mode.
        rank = (p / 100.0) * (len(sorted_s) - 1)
        lo = int(rank)
        hi = min(lo + 1, len(sorted_s) - 1)
        frac = rank - lo
        return sorted_s[lo] + (sorted_s[hi] - sorted_s[lo]) * frac

    def report(self) -> str:
        lines = ["[stub-telemetry] phase mean p50 p95 count"]
        for phase, samples in sorted(self._marks.items()):
            mean = statistics.fmean(samples) if samples else 0.0
            p50 = self.percentile_ms(phase, 50) or 0.0
            p95 = self.percentile_ms(phase, 95) or 0.0
            lines.append(
                f"  {phase:32s} {mean:8.2f} {p50:8.2f} {p95:8.2f} {len(samples):6d}",
            )
        return "\n".join(lines)

    # --- internals --------------------------------------------------------

    def _append_row(self, phase: str, ms: float) -> None:
        new_file = not self.out_csv.exists()
        try:
            self.out_csv.parent.mkdir(parents=True, exist_ok=True)
            with self.out_csv.open("a", newline="") as f:
                writer = csv.writer(f)
                if new_file:
                    writer.writerow(["turn_idx", "phase", "ms"])
                writer.writerow([self._turn_idx, phase, f"{ms:.3f}"])
        except OSError:
            # Telemetry is best-effort; don't bring down the test on
            # disk hiccups.
            pass


def _make_telemetry(csv_path: Path) -> Any:
    """Return a real ``sim.telemetry.Telemetry`` if available, else a stub.

    The real class' constructor takes ``out_csv``. We always pass the
    test-scoped path so the per-test CSV stays isolated.
    """
    try:
        from sim import telemetry as _t  # type: ignore[import-not-found]
        cls = getattr(_t, "Telemetry", None)
        if cls is None:
            return _StubTelemetry(csv_path)
        try:
            instance = cls(out_csv=str(csv_path))
        except TypeError:
            instance = cls(str(csv_path))
        # Add ``percentile_ms`` if the real class lacks it (we can't know
        # what the sibling agent ships ahead of time).
        if not hasattr(instance, "percentile_ms"):
            samples_attr = None
            for attr in ("_marks", "marks", "_samples"):
                if hasattr(instance, attr):
                    samples_attr = attr
                    break

            def _percentile_ms(phase: str, p: float,
                               _self: Any = instance,
                               _attr: str | None = samples_attr) -> float | None:
                if _attr is None:
                    return None
                samples = (getattr(_self, _attr, {}) or {}).get(phase) or []
                if not samples:
                    return None
                if len(samples) == 1:
                    return float(samples[0])
                sorted_s = sorted(float(s) for s in samples)
                rank = (p / 100.0) * (len(sorted_s) - 1)
                lo = int(rank)
                hi = min(lo + 1, len(sorted_s) - 1)
                frac = rank - lo
                return sorted_s[lo] + (sorted_s[hi] - sorted_s[lo]) * frac

            instance.percentile_ms = _percentile_ms  # type: ignore[attr-defined]
        return instance
    except Exception:
        return _StubTelemetry(csv_path)


@pytest.fixture
def telemetry(tmp_path: Path) -> Iterator[Any]:
    """Per-test telemetry instance writing to ``tmp_path/sim_latency.csv``.

    ``tmp_path`` is per-test, so two parallel tests can never clobber each
    other's CSV. The instance always exposes ``percentile_ms`` — either
    from the real ``sim.telemetry.Telemetry`` (when available) or via the
    stub adapter so e2e assertions keep working pre-merge.
    """
    csv_path = tmp_path / "sim_latency.csv"
    yield _make_telemetry(csv_path)


# ─────────────────────────────────────────────────────────────────────────────
# Misc helpers used by the e2e tests. Kept at module level so they're
# importable as ``from sim.conftest import _drain_metrics`` etc. — pytest
# happily exposes plain helpers from a conftest that's been registered
# via ``pytest_plugins``.
# ─────────────────────────────────────────────────────────────────────────────


def _scrape_metrics_text(handle: WsServerHandle, timeout_s: float = 2.0) -> str:
    """GET ``/metrics`` from the running ws server. Returns the raw text.

    Used by the ``test_metrics_latency_phase_recorded`` case. Falls back
    to an empty string on failure so the test can produce a clean
    assertion message rather than a network exception trace.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(handle.url + "/metrics",
                                    timeout=timeout_s) as resp:
            data = resp.read()
        return data.decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return ""


def _phase_count_from_metrics(metrics_text: str, phase: str) -> int | None:
    """Extract ``nao_phase_latency_ms_count{phase="<phase>"}`` from
    Prometheus exposition text. Returns the integer count or ``None`` if
    the series is absent.
    """
    if not metrics_text:
        return None
    needle = f'nao_phase_latency_ms_count{{phase="{phase}"}}'
    for line in metrics_text.splitlines():
        if line.startswith(needle):
            # Format: ``<name>{labels} <value>``
            try:
                value = line.split()[-1]
                return int(float(value))
            except (ValueError, IndexError):
                return None
    return None


# Make sure the helpers above survive ``from sim.conftest import *``.
__all__ = [
    "WsServerHandle",
    "installed_fakes",
    "boot_ws_server",
    "mocked_openai",
    "mocked_cs_navigator",
    "telemetry",
    "_scrape_metrics_text",
    "_phase_count_from_metrics",
]


# Belt-and-suspenders: when this file is loaded as a regular conftest under
# ``sim/`` and pytest still hasn't picked up ``sim/__init__.py`` (because
# it's owned by another agent), make the package discoverable so
# ``pytest_plugins=("sim.conftest",)`` from the e2e test still resolves.
_sim_dir = Path(__file__).resolve().parent
_sim_pkg = sys.modules.get("sim")
if _sim_pkg is None and _sim_dir.exists():
    # Synthesize a minimal namespace package so the plugin import path
    # works even before ``sim/__init__.py`` ships.
    import types
    pkg = types.ModuleType("sim")
    pkg.__path__ = [str(_sim_dir)]  # type: ignore[attr-defined]
    sys.modules["sim"] = pkg
