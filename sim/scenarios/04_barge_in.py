"""Scenario 04 — Barge-in mid-TTS.

Steps:
    1. Wake on a known face.
    2. User asks a long question; agent stub returns a multi-sentence reply.
    3. Mid-TTS we send a ``barge_in`` control frame.
    4. Assert: the player stops within 600 ms (no more audio_chunks for
       at least 0.6 s after we send barge_in).
    5. Send a follow-up utterance "thanks" — assert it triggers a *new*
       turn (turn index increments) instead of being treated as more of
       the prior utterance.

Server-side note: per ``server/app_ws.py`` the ``barge_in`` control
currently logs the event (the TTS abort path is wired in Phase 2). The
scenario records the latency budget regardless — when the abort lands on
the server, the same scenario will catch a regression if the player
keeps streaming.
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


SCENARIO_NAME = "04_barge_in"
SCENARIO_TIMEOUT_S = 30.0
BARGE_IN_BUDGET_MS = 600.0


def run(driver: Driver, telemetry: Any) -> dict[str, Any]:
    deadline = time.monotonic() + SCENARIO_TIMEOUT_S
    details: dict[str, Any] = {}

    try:
        # A long-ish reply gives the TTS pipeline something to interrupt.
        long_reply = (
            "There are a few things to consider here. First, the prerequisites "
            "are CS 351 and CS 451 with grades of C or better. Second, the "
            "scheduling rotation alternates between fall and spring depending "
            "on faculty availability. Third, your advisor will need to sign "
            "off on the registration override before you can enroll."
        )
        driver.install_mocks(
            transcript="tell me about the CS 491 prerequisites in detail",
            reply=long_reply,
            actions=[],
            crisis=False,
            active_agent="chatbot",
            # Slow each TTS chunk so we can interleave the barge_in.
            tts_per_chunk_delay_ms=300,
        )
        driver.connect_ws("ws://localhost/ws/aayush")

        # Wake
        telemetry.start_turn(0, "<wake>")
        driver.inject_face(face_id="aayush", confidence=0.88, distance_m=0.9)
        driver.expect(predicate_control("ready_to_listen"),
                      timeout_s=min(5.0, deadline - time.monotonic()))
        # Wait for wake-greeting tts_ended + cooldown to clear before
        # driving the question turn (otherwise audio_chunks get dropped).
        try:
            driver.expect(predicate_control("tts_ended"),
                          timeout_s=min(3.0, deadline - time.monotonic()))
            time.sleep(0.7)
        except TimeoutError:
            pass
        telemetry.end_turn("ok", "wake handled")

        # Question — anchor a cursor so we don't accidentally consume the
        # wake-greeting audio_chunk that fires right before ready_to_listen.
        cursor1 = driver.cursor()
        telemetry.start_turn(1, "tell me about the CS 491 prerequisites in detail")
        t0 = time.perf_counter()
        driver.say("tell me about the CS 491 prerequisites in detail")

        # Wait for first audio chunk so we know TTS started.
        first_audio = driver.expect(
            predicate_audio_chunk(),
            timeout_s=min(5.0, deadline - time.monotonic()),
            since=cursor1,
        )
        first_audio_seq = first_audio.get("seq")
        telemetry.mark("e2e_user_to_first_audio",
                       (time.perf_counter() - t0) * 1000.0)

        # Send barge_in.
        t_barge = time.perf_counter()
        driver.send_barge_in()

        # Verify no NEW audio chunks land within the 600 ms budget. The
        # `assert_no_more_audio` helper raises AssertionError on a violation
        # but we want a soft outcome here so we can continue the scenario.
        try:
            driver.assert_no_more_audio(timeout_s=BARGE_IN_BUDGET_MS / 1000.0)
            stopped_in_budget = True
        except AssertionError as ae:
            stopped_in_budget = False
            details["barge_in_violation"] = str(ae)
        details["stopped_in_budget"] = stopped_in_budget
        details["first_audio_seq"] = first_audio_seq
        if not stopped_in_budget:
            details["barge_in_note"] = (
                "barge_in arrived but TTS kept emitting chunks within the "
                "{}-ms budget. Check server/app_ws.py:_emit_agent_turn — the "
                "sentence-streaming loop should break on sess.barge_event."
            ).format(BARGE_IN_BUDGET_MS)
        # Phase 10.5: server now emits a `tts_aborted` control frame when
        # the barge_event interrupts the TTS loop. We DON'T require it to
        # land (it only fires if the LLM actually had more sentences left
        # to synthesize at the moment of the barge), but we record whether
        # it appeared for diagnostic purposes.
        try:
            tts_aborted = driver.expect(
                lambda f: (f.get("type") == "control"
                           and f.get("subtype") == "tts_aborted"),
                timeout_s=0.5,
                since=cursor1,
            )
            details["tts_aborted_seen"] = True
            details["tts_aborted_sent_chunks"] = (
                (tts_aborted.get("data") or {}).get("sent_chunks")
            )
        except TimeoutError:
            details["tts_aborted_seen"] = False
        telemetry.mark("action_dispatch",
                       (time.perf_counter() - t_barge) * 1000.0)
        telemetry.end_turn("ok" if stopped_in_budget else "fail",
                           "barge_in observed")

        # Step 5 — follow-up utterance becomes a new turn.
        cursor2 = driver.cursor()
        telemetry.start_turn(2, "thanks")
        # Override the mock for the follow-up turn so the reply differs.
        driver.install_mocks(
            transcript="thanks",
            reply="You got it.",
            actions=[],
            crisis=False,
            active_agent="chat",
        )
        t1 = time.perf_counter()
        driver.say("thanks")

        # We assert a NEW transcript control arrives — a regression where
        # the second audio gets folded into the first turn would skip the
        # second transcript entirely.
        baseline_transcripts = sum(
            1 for f in driver.ws.frames_snapshot()
            if f.get("type") == "control" and f.get("subtype") == "transcript"
        )
        thanks_transcript = driver.expect(
            lambda f: (
                f.get("type") == "control"
                and f.get("subtype") == "transcript"
                and (f.get("data") or {}).get("transcript") == "thanks"
            ),
            timeout_s=min(10.0, deadline - time.monotonic()),
            since=cursor2,
        )
        # The frames AFTER the "thanks" transcript belong to the new turn.
        # If we used cursor2 directly, residual audio_chunks from the
        # un-aborted prior turn would race in front of the new turn's
        # output and the assertion would attribute them to the new turn.
        snapshot = driver.ws.frames_snapshot()
        try:
            thanks_idx = snapshot.index(thanks_transcript)
        except ValueError:
            thanks_idx = cursor2
        new_audio = driver.expect(
            predicate_audio_chunk(),
            timeout_s=min(5.0, deadline - time.monotonic()),
            since=thanks_idx + 1,
        )
        details["second_turn_audio_text"] = new_audio.get("text")
        details["baseline_transcripts"] = baseline_transcripts
        telemetry.mark("e2e_user_to_complete",
                       (time.perf_counter() - t1) * 1000.0)
        telemetry.end_turn("ok", new_audio.get("text") or "")

        return {"scenario": SCENARIO_NAME,
                "outcome": "ok" if stopped_in_budget else "fail",
                "details": details, "telemetry_rows": telemetry.rows}
    except DriverUnavailable as e:
        return {"scenario": SCENARIO_NAME, "outcome": "skipped",
                "details": {"reason": str(e)}, "telemetry_rows": telemetry.rows}
    except TimeoutError as e:
        try:
            telemetry.end_turn("timeout", "")
        except Exception:
            pass
        return {"scenario": SCENARIO_NAME, "outcome": "timeout",
                "details": {"reason": str(e), **details},
                "telemetry_rows": telemetry.rows}
    except Exception as e:  # noqa: BLE001
        try:
            telemetry.end_turn("fail", "")
        except Exception:
            pass
        return {"scenario": SCENARIO_NAME, "outcome": "fail",
                "details": {"reason": repr(e), **details},
                "telemetry_rows": telemetry.rows}
