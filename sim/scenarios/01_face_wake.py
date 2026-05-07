"""Scenario 01 — Face wake.

Steps:
    1. Connect WS, send session_open (driver does this automatically).
    2. Inject a known-face wake_event for ``aayush`` with confidence 0.9.
    3. Expect the server to log a wake_event and emit a ``ready_to_listen``
       control. (Returning users would also get a greeting audio_chunk; we
       force the new-user path here so the test stays deterministic across
       freshly-cleared sqlite states. The 03_therapy_turn scenario hits the
       returning-user greeting branch.)
    4. Speak "hello" — drive a turn through the agent stub.
    5. Expect a ``transcript`` control + at least one ``audio_chunk``.

Outcome: ``ok`` if every expectation lands within the 30-second budget,
``timeout`` if any single ``expect()`` raises TimeoutError, ``fail`` for
anything else.
"""
from __future__ import annotations

import time
from typing import Any

from sim.scenarios._driver import (
    Driver,
    DriverUnavailable,
    predicate_audio_chunk,
    predicate_control,
)


SCENARIO_NAME = "01_face_wake"
SCENARIO_TIMEOUT_S = 30.0


def run(driver: Driver, telemetry: Any) -> dict[str, Any]:
    deadline = time.monotonic() + SCENARIO_TIMEOUT_S
    details: dict[str, Any] = {}

    try:
        driver.install_mocks(
            transcript="hello",
            reply="Hi there.",
            actions=[],
            crisis=False,
            active_agent="chat",
        )
        driver.connect_ws("ws://localhost/ws/aayush")

        # Step 1 — wake event
        telemetry.start_turn(0, "<wake>")
        t0 = time.perf_counter()
        driver.inject_face(face_id="aayush", confidence=0.9, distance_m=0.8,
                           gate="voice")

        # Step 2 — wait for ready_to_listen (always emitted by the wake handler)
        rtl = driver.expect(predicate_control("ready_to_listen"),
                            timeout_s=min(5.0, deadline - time.monotonic()))
        wake_to_ready_ms = (time.perf_counter() - t0) * 1000.0
        telemetry.mark("wake_to_engaged", wake_to_ready_ms)
        details["ready_to_listen"] = rtl.get("data", {})

        # The returning-user greeting fires BEFORE ready_to_listen, so by
        # the time we reach this point the wake-greeting audio_chunk +
        # tts_ended are already in the frame buffer. Note them on the
        # details so the scenario assertion is unambiguous about which
        # audio belongs to the wake handler vs the user-turn path.
        wake_audio = next(
            (f for f in driver.ws.frames_snapshot()
             if f.get("type") == "audio_chunk"),
            None,
        )
        if wake_audio is not None:
            details["wake_greeting_audio_text"] = wake_audio.get("text")
        telemetry.end_turn("ok", "wake handled")

        # Step 3 — speak hello, drive a turn. We anchor the frame cursor so
        # subsequent expect() calls only see frames produced by the user-turn
        # path (not the wake greeting we already drained).
        cursor = driver.cursor()
        telemetry.start_turn(1, "hello")
        t1 = time.perf_counter()
        driver.say("hello")

        transcript = driver.expect(
            predicate_control("transcript"),
            timeout_s=min(5.0, deadline - time.monotonic()),
            since=cursor,
        )
        t_transcript = time.perf_counter()
        telemetry.mark("stt", (t_transcript - t1) * 1000.0)

        audio = driver.expect(
            predicate_audio_chunk(),
            timeout_s=min(5.0, deadline - time.monotonic()),
            since=cursor,
        )
        t_audio = time.perf_counter()
        telemetry.mark("e2e_user_to_first_audio", (t_audio - t1) * 1000.0)
        telemetry.mark("tts_synth_first_chunk",
                       (t_audio - t_transcript) * 1000.0)

        driver.expect(
            predicate_control("tts_ended"),
            timeout_s=min(5.0, deadline - time.monotonic()),
            since=cursor,
        )
        telemetry.mark("e2e_user_to_complete",
                       (time.perf_counter() - t1) * 1000.0)

        details["transcript"] = transcript.get("data", {}).get("transcript")
        details["reply_audio_text"] = audio.get("text")

        telemetry.end_turn("ok", audio.get("text") or "")
        return {
            "scenario": SCENARIO_NAME,
            "outcome": "ok",
            "details": details,
            "telemetry_rows": telemetry.rows,
        }
    except DriverUnavailable as e:
        return {"scenario": SCENARIO_NAME, "outcome": "skipped",
                "details": {"reason": str(e)}, "telemetry_rows": telemetry.rows}
    except TimeoutError as e:
        telemetry.end_turn("timeout", "")
        return {"scenario": SCENARIO_NAME, "outcome": "timeout",
                "details": {"reason": str(e)}, "telemetry_rows": telemetry.rows}
    except Exception as e:  # noqa: BLE001
        try:
            telemetry.end_turn("fail", "")
        except Exception:
            pass
        return {"scenario": SCENARIO_NAME, "outcome": "fail",
                "details": {"reason": repr(e)},
                "telemetry_rows": telemetry.rows}
