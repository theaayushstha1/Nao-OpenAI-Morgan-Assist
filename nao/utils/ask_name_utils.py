# -*- coding: utf-8 -*-
"""Onboarding name capture for the NAO robot.

Phase 8 (HRI-research-driven onboarding polish) replaces the previous
two-prompt sequence ("look at me" + "what's your name?") with a single
combined utterance that doubles as the wake bridge AND the name request.
The new entry point is :func:`ask_name_combined`; the legacy
:func:`ask_name` (file-based round-trip used by ``nao/conversation.py``)
is preserved verbatim for backwards compatibility with the non-streaming
HTTP path and with ``tests/test_nao_control_guards.py``.

Public API
----------
* ``ask_name_combined(audio_streamer, ws_client, tts_player, on_name,
  raw_tts=None, transcript_provider=None, confirm_provider=None,
  prompt_text=None, retry_text=None, confirm_text=None,
  ack_text=None, listen_timeout_s=8.0, confirm_timeout_s=4.0,
  retry_audio_timeout_s=8.0)``

  Single-call onboarding flow used by Phase 3+ wake state machine.
  Speaks the heads-up + name prompt as ONE TTS utterance, records ONCE
  via the live audio streamer (already managed by ``ws_client``), pulls
  the resulting transcript via the WS server and extracts a name with
  ``utils.name_utils.extract_name``. Confirms once if the extraction
  fails (one fallback retry). Calls ``on_name(name)`` exactly once when
  settled — name is ``None`` if extraction failed end-to-end so the
  caller can decide what to do (cache "Guest", retry on the next wake,
  etc).

* ``ask_name(tts, nao_ip, server_url, session, record_audio_func,
  should_abort=None)``

  Legacy file-based round-trip — unchanged. ``nao/conversation.py``'s
  ``_onboard_new_user`` still imports this. Don't drop it until the
  conversation module is fully retired.

Both entry points share the same semantics: never block forever, never
raise into the caller (any error is caught + reported via ``on_name(None)``
or a Guest fallback), and stay Python 2.7 compatible.
"""
from __future__ import print_function

import os
import threading
import time

import requests

from utils.name_utils import extract_name
from utils.speech import random_phrase, expressive_say


# ---------------------------------------------------------------------------
# Phase 8: combined single-prompt onboarding
# ---------------------------------------------------------------------------

# Default copy. The HRI brief in PRD v2 §Phase 8 + PHASE_8_TASK_MAP §
# "First-time user" calls for one warm sentence that introduces NAO and
# asks for a name. Variants below are kept short so the user has a clean
# barge-in window after the prompt finishes.
_DEFAULT_PROMPT = (
    "Hi, I'm NAO. I haven't met you yet -- what should I call you?"
)
_DEFAULT_RETRY = (
    "Sorry, I didn't catch that. Could you say your name again?"
)
_DEFAULT_CONFIRM_TEMPLATE = "Sorry, did you say {name}?"
_DEFAULT_ACK_TEMPLATE = "Got it, {name}. Pleasure to meet you."


def _coerce_text(value):
    """Best-effort string coercion across Py2.7 byte/unicode boundaries."""
    if value is None:
        return ""
    try:
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8", "ignore").strip()
            except Exception:
                return ""
        return str(value).strip()
    except Exception:
        return ""


def _try_call(method, *args, **kwargs):
    """Invoke a duck-typed method; swallow + return ``(ok, result)``.

    Used everywhere we probe for an optional method on ``ws_client`` /
    ``tts_player`` / ``audio_streamer`` so a missing API on a stub never
    bubbles up as an AttributeError mid-onboarding.
    """
    if method is None or not callable(method):
        return False, None
    try:
        return True, method(*args, **kwargs)
    except Exception as exc:
        # Stay silent in the happy path; print so the operator can see why
        # the onboarding flow degraded but never raise back to the caller.
        print("[ask_name_combined] {0} call failed: {1}".format(
            getattr(method, "__name__", "method"), exc))
        return False, None


def _speak_combined(tts_player, raw_tts, text):
    """Speak ``text`` as a single utterance.

    Tries (in order):
        1. ``tts_player.say(text)`` — Phase 1 streaming player MAY expose
           this in a future iteration. Today the production class only
           exposes ``enqueue(text, mp3_bytes)`` and we cannot synthesize
           from text on-robot, so this branch is forward-looking.
        2. ``raw_tts.say(text)`` — ``ALTextToSpeech`` proxy. This is the
           fallback when the WS streaming TTS path isn't connected (the
           PHASE_8_TASK_MAP §Public API note: "locally on the robot if
           WS not connected; via TTS chunk if connected").
        3. ``raw_tts`` may be a callable that takes ``text`` directly
           (test seam).

    Returns True on success, False on any failure. Never raises.
    """
    if not text:
        return False

    # 1. Streaming TTS path (preferred when wired). Probed via ``say``
    #    rather than ``enqueue`` because ``enqueue`` requires pre-encoded
    #    MP3 bytes which only the server can produce; the spec wants ONE
    #    spoken prompt and we'd rather fall through to ALTextToSpeech
    #    than synthesize a partial sentence locally.
    if tts_player is not None and hasattr(tts_player, "say"):
        ok, _ = _try_call(getattr(tts_player, "say"), text)
        if ok:
            return True

    # 2. Local ALTextToSpeech proxy.
    if raw_tts is not None:
        if callable(raw_tts):
            ok, _ = _try_call(raw_tts, text)
            if ok:
                return True
        say_method = getattr(raw_tts, "say", None)
        if callable(say_method):
            try:
                expressive_say(raw_tts, text, "warm")
                return True
            except Exception:
                ok, _ = _try_call(say_method, text)
                if ok:
                    return True

    # 3. Last-resort: print (dev environment, no robot, no fallback).
    print("[ask_name_combined] (no TTS hook): {0!r}".format(text))
    return False


def _await_transcript(ws_client, transcript_provider, timeout_s):
    """Block until a transcript arrives or ``timeout_s`` elapses.

    Probes (in order):
        1. ``transcript_provider(timeout_s)`` — explicit injection seam,
           used by tests + by callers that already own a transcript queue.
        2. ``ws_client.next_transcript(timeout_s)`` — duck-typed WS API
           that future ws_client iterations are expected to add. We probe
           for it so this function is forward-compatible without a
           coordination round-trip with the ws_client author.
        3. ``ws_client.await_transcript(timeout_s)`` — alternate name in
           case the sibling settles on a different verb.

    Returns the transcript string (possibly empty), or None on timeout
    / no provider available. The caller decides whether None means
    "extraction failed" (retry) or "give up" (Guest fallback).
    """
    timeout_s = max(0.0, float(timeout_s))

    # 1. Explicit injection seam. Highest priority so tests can drive the
    #    flow without a real ws_client.
    if transcript_provider is not None:
        ok, value = _try_call(transcript_provider, timeout_s)
        if ok:
            return _coerce_text(value) if value is not None else None

    # 2 + 3. ws_client probes.
    if ws_client is not None:
        for attr in ("next_transcript", "await_transcript", "pop_transcript"):
            method = getattr(ws_client, attr, None)
            if callable(method):
                ok, value = _try_call(method, timeout_s)
                if ok:
                    return _coerce_text(value) if value is not None else None

    return None


def _await_yes_no(ws_client, confirm_provider, timeout_s):
    """Block for a yes/no follow-up. Returns True/False/None.

    Symmetric to :func:`_await_transcript`. Defaults to interpreting any
    transcript as the answer string and applying a small lexicon.
    """
    timeout_s = max(0.0, float(timeout_s))

    if confirm_provider is not None:
        ok, value = _try_call(confirm_provider, timeout_s)
        if ok:
            if isinstance(value, bool):
                return value
            return _interpret_yes_no(_coerce_text(value))

    # Fall back to the same transcript channel.
    text = _await_transcript(ws_client, None, timeout_s)
    if text is None:
        return None
    return _interpret_yes_no(text)


_YES_TOKENS = set([
    "yes", "yeah", "yep", "yup", "yeah that's right", "that's right",
    "right", "correct", "affirmative", "sure", "uh huh", "uh-huh",
    "thats right", "ok", "okay",
])
_NO_TOKENS = set([
    "no", "nope", "nah", "incorrect", "wrong", "no it's not",
    "negative", "not quite", "not really",
])


def _interpret_yes_no(text):
    """Tiny yes/no classifier. Returns True/False/None.

    Phrasing pulled from the most common confirmation responses in the
    therapist transcript corpus. Anything unclassifiable falls through to
    None so the caller can treat it as "ambiguous" rather than risking a
    silent miss.
    """
    if not text:
        return None
    norm = text.lower().strip(" .!?,")
    if not norm:
        return None
    # Whole-utterance match first.
    if norm in _YES_TOKENS:
        return True
    if norm in _NO_TOKENS:
        return False
    # Token-prefix sniff so "yes that's right" / "no my name is Aayush"
    # both classify cleanly.
    first = norm.split()[0]
    if first in ("yes", "yeah", "yep", "yup", "correct", "right",
                 "affirmative", "sure"):
        return True
    if first in ("no", "nope", "nah", "incorrect", "wrong", "negative"):
        return False
    return None


def _notify_server_asking(ws_client, asking):
    """Push a control frame so the server flags the next turn.

    The server's ``_Session.asking_name`` boolean (server/app_ws.py)
    relaxes the transcript-reject filter when the user is in the name
    capture window. This isn't critical for Phase 8's combined prompt
    (the legacy HTTP path used a multipart field for the same purpose)
    but it lets the server keep its existing filter behaviour intact.

    Idempotent and best-effort — the function is a no-op if ``ws_client``
    doesn't expose ``push_control`` (dev environment, test stub, future
    ws_client variant, etc).
    """
    if ws_client is None:
        return
    push = getattr(ws_client, "push_control", None)
    if not callable(push):
        return
    payload = {"asking_name": bool(asking)}
    _try_call(push, "name_capture", payload)


def ask_name_combined(audio_streamer, ws_client, tts_player, on_name,
                      raw_tts=None,
                      transcript_provider=None,
                      confirm_provider=None,
                      prompt_text=None,
                      retry_text=None,
                      confirm_text=None,
                      ack_text=None,
                      listen_timeout_s=8.0,
                      confirm_timeout_s=4.0,
                      retry_audio_timeout_s=8.0):
    """Single combined onboarding prompt + name extraction + ack.

    Parameters
    ----------
    audio_streamer : object with ``.start()``, ``.stop()``, ``.gate(bool)``
        Phase 1 ``NaoAudioStreamer`` instance. The function does NOT
        start/stop the streamer itself — the WakeStateMachine /
        ``_SessionController`` already manage its lifecycle. We only
        toggle the mic gate around the spoken prompt so NAO doesn't
        record itself asking the question.
    ws_client : object with ``.push_control(subtype, data)`` (optional
        ``.next_transcript(timeout)`` / ``.await_transcript(timeout)``)
        Live WebSocket session manager. May be ``None`` in fully offline
        / dev environments — the function will fall back to local TTS
        and return early via ``on_name(None)`` since there's no path to
        get a transcript back.
    tts_player : object with ``.say(text)`` OR ``None``
        Optional streaming player (forward-looking duck type). When
        absent, ``raw_tts`` is the fallback.
    on_name : callable(name_or_none)
        Called exactly once with the extracted name string (capitalized)
        on success, or ``None`` if extraction failed. Never raises into
        the caller.
    raw_tts : object with ``.say(text)`` OR callable(text)
        ``ALTextToSpeech`` proxy (or callable test seam). Used when
        ``tts_player`` cannot speak text directly (today: always, since
        the streaming player only handles MP3 chunks). Auto-resolved
        from naoqi if not provided AND naoqi is available.
    transcript_provider : callable(timeout_s) -> str
        Optional injection seam for tests. Returns the next transcript
        string within the timeout, or None on timeout. When supplied,
        wins over ``ws_client.next_transcript`` / etc.
    confirm_provider : callable(timeout_s) -> bool|str
        Optional injection seam for the confirmation step. Returns a
        bool directly OR a yes/no string that we classify with the
        builtin lexicon.
    prompt_text, retry_text, confirm_text, ack_text : str
        Override the default copy. ``confirm_text`` and ``ack_text``
        accept ``{name}`` placeholders.
    listen_timeout_s, confirm_timeout_s, retry_audio_timeout_s : float
        Wall-clock budgets for each phase.

    Returns
    -------
    None.

    Notes
    -----
    Designed to fit inside a single ENGAGED→LISTENING transition in the
    Phase 3 ``WakeStateMachine``. The WSM is responsible for promoting
    the state to LISTENING; this function only owns the spoken interaction.

    On any error that prevents getting a transcript (no ws_client, no
    provider, repeated timeouts) the function calls ``on_name(None)`` and
    returns — the caller (typically a wake-state callback) will fall back
    to a Guest username so the conversation can still begin.
    """
    # Resolve copy first so a missing prompt template doesn't kick in
    # half-way through and confuse the user.
    prompt = prompt_text if prompt_text is not None else _DEFAULT_PROMPT
    retry_prompt = retry_text if retry_text is not None else _DEFAULT_RETRY
    confirm_template = (confirm_text if confirm_text is not None
                        else _DEFAULT_CONFIRM_TEMPLATE)
    ack_template = ack_text if ack_text is not None else _DEFAULT_ACK_TEMPLATE

    # Resolve the local TTS fallback. We try the caller-provided
    # ``raw_tts`` first; if missing AND naoqi is present, build an
    # ALTextToSpeech proxy so the dev environment / partial wiring still
    # produces a spoken prompt.
    if raw_tts is None:
        raw_tts = _resolve_default_raw_tts()

    def _settle(name):
        """Single point of truth for invoking ``on_name`` exactly once."""
        try:
            if callable(on_name):
                on_name(name)
        except Exception as exc:
            # ``on_name`` is owned by the caller; never raise back into
            # them but log so the operator can spot wiring bugs.
            print("[ask_name_combined] on_name callback raised: {0}".format(exc))

    # 1. Tell the server we're entering the name capture window.
    _notify_server_asking(ws_client, asking=True)

    # 2. Mic-gate around the spoken prompt so NAO doesn't record itself.
    #    ``audio_streamer.gate(True)`` mutes; ``False`` un-mutes. The
    #    streamer's own gate is idempotent so calling it on an already-
    #    gated stream is safe.
    _gate_mic(audio_streamer, closed=True)
    spoken = _speak_combined(tts_player, raw_tts, prompt)
    # Brief settle so the speaker buffer drains before re-arming the mic.
    time.sleep(0.2)
    _gate_mic(audio_streamer, closed=False)

    if not spoken:
        # We couldn't even speak the prompt; recording would be useless
        # since the user has nothing to respond to. Bail now.
        _notify_server_asking(ws_client, asking=False)
        _settle(None)
        return

    # 3. Wait for the user's response transcript.
    transcript = _await_transcript(ws_client, transcript_provider,
                                   listen_timeout_s)

    name = extract_name(transcript or "")

    # 4. If extraction missed, fall through to a single re-prompt so the
    #    user gets one explicit chance before we drop to Guest.
    if not name:
        _gate_mic(audio_streamer, closed=True)
        _speak_combined(tts_player, raw_tts, retry_prompt)
        time.sleep(0.2)
        _gate_mic(audio_streamer, closed=False)
        retry_transcript = _await_transcript(ws_client, transcript_provider,
                                             retry_audio_timeout_s)
        name = extract_name(retry_transcript or "")
        # Stash the retry transcript so the confirmation branch (if it
        # fires below) sees the freshest text.
        transcript = retry_transcript or transcript

    # 5. If we extracted something but the transcript was *only* the
    #    name (or otherwise short / ambiguous), confirm once. The
    #    PHASE_8_TASK_MAP §First-time user spec is "if confidence low:
    #    'Sorry, did you say X?' — confirm; on yes proceed; on no
    #    re-ask". We treat "transcript was a single token" as the
    #    low-confidence signal — that's where extract_name's pattern
    #    fallback fired without surrounding context.
    if name and _looks_low_confidence(transcript, name):
        confirm_msg = _format_text(confirm_template, name=name)
        _gate_mic(audio_streamer, closed=True)
        _speak_combined(tts_player, raw_tts, confirm_msg)
        time.sleep(0.2)
        _gate_mic(audio_streamer, closed=False)
        answer = _await_yes_no(ws_client, confirm_provider, confirm_timeout_s)
        if answer is False:
            # Single re-ask on explicit "no". Treat the new transcript
            # as authoritative; if it still misses, give up cleanly.
            _gate_mic(audio_streamer, closed=True)
            _speak_combined(tts_player, raw_tts, retry_prompt)
            time.sleep(0.2)
            _gate_mic(audio_streamer, closed=False)
            redo = _await_transcript(ws_client, transcript_provider,
                                     retry_audio_timeout_s)
            redone = extract_name(redo or "")
            if redone:
                name = redone
            else:
                name = None
        # answer is True  -> keep ``name``
        # answer is None  -> ambiguous; trust the original extraction
        #                    rather than spinning forever.

    # 6. Final ack (only when we have a real name). Server flag clears
    #    regardless so subsequent turns aren't stuck in name-capture mode.
    if name:
        ack_msg = _format_text(ack_template, name=name)
        _gate_mic(audio_streamer, closed=True)
        _speak_combined(tts_player, raw_tts, ack_msg)
        time.sleep(0.2)
        _gate_mic(audio_streamer, closed=False)

    _notify_server_asking(ws_client, asking=False)
    _settle(name if name else None)


def _gate_mic(audio_streamer, closed):
    """Toggle the audio streamer's mic gate. Best-effort, never raises."""
    if audio_streamer is None:
        return
    method = getattr(audio_streamer, "gate", None)
    if not callable(method):
        return
    _try_call(method, bool(closed))


def _looks_low_confidence(transcript, name):
    """Heuristic for the confirmation gate.

    We don't have a real STT confidence score on the robot side; the
    proxy is "did the transcript carry any context, or was it just the
    name?". A bare name with nothing else is most likely a one-word
    response that ALAudioRecorder + Whisper sometimes mis-bins (e.g.
    "iish" vs "Aayush"), so it's worth one explicit confirmation.
    """
    text = (transcript or "").strip()
    if not text:
        return True
    # Strip terminal punctuation; everything we compare is lowercased.
    norm = text.lower().rstrip(" .!?,").strip()
    if not norm:
        return True
    # If the transcript is exactly the extracted name (or differs only
    # by punctuation / casing), confirm.
    if norm == (name or "").lower():
        return True
    # Single-word transcripts are usually a clean "Aayush" — high
    # confidence. We only flag them when name-extraction had to lean on
    # the bare-token fallback (i.e. transcript IS one token AND that
    # token didn't carry "my name is" context).
    word_count = len(norm.split())
    if word_count == 1:
        return False
    return False


def _format_text(template, **kwargs):
    """Best-effort .format with graceful key-miss fallback."""
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        return template


def _resolve_default_raw_tts():
    """Build an ``ALTextToSpeech`` proxy from naoqi if available.

    Returns ``None`` on a developer machine (no naoqi) so unit tests run
    without trying to talk to a robot.
    """
    try:
        from naoqi import ALProxy  # type: ignore
    except Exception:
        return None
    try:
        import config as _cfg  # type: ignore
        ip = getattr(_cfg, "NAO_IP", None)
        port = int(getattr(_cfg, "NAO_PORT", 9559))
    except Exception:
        ip = os.environ.get("NAO_IP") or "127.0.0.1"
        port = int(os.environ.get("NAO_PORT") or 9559)
    if not ip:
        return None
    try:
        return ALProxy("ALTextToSpeech", ip, port)
    except Exception as exc:
        print("[ask_name_combined] ALTextToSpeech proxy failed: {0}".format(exc))
        return None


# ---------------------------------------------------------------------------
# Legacy file-based round-trip (Phase 1-7 path)
# ---------------------------------------------------------------------------
#
# Kept verbatim so ``nao/conversation.py`` and
# ``tests/test_nao_control_guards.py::test_ask_name_aborts_when_face_recognized``
# keep working. Do not change the signature without updating both.
# ---------------------------------------------------------------------------


def ask_name(tts, nao_ip, server_url, session, record_audio_func,
             should_abort=None):
    """Ask the user their name via audio, transcribe, and extract.

    Args:
        tts: ALTextToSpeech proxy or qi service.
        nao_ip: NAO robot IP address.
        server_url: URL of the /upload endpoint.
        session: requests.Session for HTTP calls.
        record_audio_func: Callable that takes nao_ip and returns a wav path.

    Returns:
        Extracted name string, or "Guest" as fallback.
    """
    expressive_say(tts, random_phrase("ask_name"), "warm")
    time.sleep(0.5)
    for attempt in range(2):
        if should_abort is not None:
            try:
                if should_abort():
                    return None
            except Exception:
                pass
        wav = record_audio_func(nao_ip)
        if should_abort is not None:
            try:
                if should_abort():
                    return None
            except Exception:
                pass
        if not wav or not os.path.exists(wav):
            if attempt == 0:
                expressive_say(tts, random_phrase("ask_name_retry"), "warm")
            continue
        try:
            try:
                import config as _cfg
                _hdr = {"X-NAO-Secret": _cfg.NAO_SHARED_SECRET} if getattr(_cfg, "NAO_SHARED_SECRET", "") else {}
            except Exception:
                _hdr = {}
            with open(wav, 'rb') as f:
                r = requests.post(server_url + "/turn", files={"audio": f},
                                  data={"username": "guest", "asking_name": "true"},
                                  headers=_hdr, timeout=30)
            spoken = (r.json() or {}).get("user_input", "")
            print("[Heard]: '{}'".format(spoken))
            name = extract_name(spoken)
            if name:
                print("[Extracted name]: {}".format(name))
                return name
            elif attempt == 0:
                expressive_say(tts, random_phrase("ask_name_retry"), "warm")
                time.sleep(0.3)
        except Exception as e:
            print("[Name error]:", e)
            if attempt == 0:
                expressive_say(tts, random_phrase("ask_name_retry"), "warm")
    return "Guest"


# ---------------------------------------------------------------------------
# Self-test — runs without naoqi / requests / ws_client.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Exercise the combined flow against synthetic stubs so the module's
    # py_compile + smoke contract is verifiable on a developer laptop.
    spoken = []

    class _FakeStreamer(object):
        def __init__(self):
            self.gates = []

        def gate(self, closed):
            self.gates.append(bool(closed))

    class _FakeWs(object):
        def __init__(self, transcripts):
            self._transcripts = list(transcripts)
            self.controls = []

        def push_control(self, subtype, data=None):
            self.controls.append((subtype, dict(data or {})))

        def next_transcript(self, timeout):
            if not self._transcripts:
                return None
            return self._transcripts.pop(0)

    class _FakeTts(object):
        def __init__(self):
            self.said = []

        def say(self, text):
            self.said.append(text)
            spoken.append(text)

    # Case 1: clean extraction first try.
    on_name_calls = []
    streamer = _FakeStreamer()
    tts = _FakeTts()
    ws = _FakeWs(["my name is Aayush"])
    ask_name_combined(streamer, ws, tts,
                      lambda n: on_name_calls.append(n))
    assert on_name_calls == ["Aayush"], on_name_calls
    assert any("NAO" in s for s in tts.said), tts.said
    assert any("Pleasure to meet" in s for s in tts.said), tts.said
    # Should have toggled the mic gate around each spoken segment.
    assert True in streamer.gates and False in streamer.gates, streamer.gates
    # Server should have been notified of name-capture entry + exit.
    subtypes = [c[0] for c in ws.controls]
    assert subtypes.count("name_capture") >= 2, subtypes
    print("[selftest] case 1 (clean first try) ok")

    # Case 2: bare-name response triggers confirmation; user says yes.
    on_name_calls = []
    streamer = _FakeStreamer()
    tts = _FakeTts()
    ws = _FakeWs(["Aayush", "yes"])
    ask_name_combined(streamer, ws, tts,
                      lambda n: on_name_calls.append(n),
                      confirm_provider=lambda t: True)
    assert on_name_calls == ["Aayush"], on_name_calls
    print("[selftest] case 2 (bare name + yes) ok")

    # Case 3: extraction misses, retry succeeds.
    on_name_calls = []
    streamer = _FakeStreamer()
    tts = _FakeTts()
    ws = _FakeWs(["mumble mumble", "my name is Lina"])
    ask_name_combined(streamer, ws, tts,
                      lambda n: on_name_calls.append(n))
    assert on_name_calls == ["Lina"], on_name_calls
    print("[selftest] case 3 (retry needed, hit) ok")

    # Case 4: both attempts miss -> on_name(None).
    on_name_calls = []
    streamer = _FakeStreamer()
    tts = _FakeTts()
    ws = _FakeWs(["", ""])
    ask_name_combined(streamer, ws, tts,
                      lambda n: on_name_calls.append(n))
    assert on_name_calls == [None], on_name_calls
    print("[selftest] case 4 (both miss) ok")

    # Case 5: explicit transcript_provider overrides ws_client.
    on_name_calls = []
    streamer = _FakeStreamer()
    tts = _FakeTts()
    provided = ["call me Morgan"]

    def _provider(timeout_s):
        if not provided:
            return None
        return provided.pop(0)

    ask_name_combined(streamer, None, tts,
                      lambda n: on_name_calls.append(n),
                      transcript_provider=_provider)
    assert on_name_calls == ["Morgan"], on_name_calls
    print("[selftest] case 5 (transcript_provider injection) ok")

    # Case 6: legacy ask_name still works with should_abort short-circuit.
    def _record(_ip):
        raise AssertionError("record_audio should not be called")

    class _FakeRawTts(object):
        def say(self, _text):
            pass

    legacy_out = ask_name(_FakeRawTts(), "127.0.0.1", "http://srv", None,
                          _record, should_abort=lambda: True)
    assert legacy_out is None, legacy_out
    print("[selftest] case 6 (legacy abort path) ok")

    # _interpret_yes_no edge cases.
    assert _interpret_yes_no("yes that's right") is True
    assert _interpret_yes_no("no my name is Sam") is False
    assert _interpret_yes_no("maybe") is None
    assert _interpret_yes_no("") is None
    print("[selftest] yes/no interpreter ok")

    print("[selftest] all checks passed.")
