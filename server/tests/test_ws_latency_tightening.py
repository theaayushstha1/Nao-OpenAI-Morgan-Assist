import asyncio
import base64

import pytest

pytest.importorskip("server.app_ws")

from server import app_ws  # noqa: E402


class _Ws:
    pass


def _audio_frame() -> dict:
    pcm = b"\x01\x00" * 320
    return {
        "type": "audio_chunk",
        "seq": 1,
        "ts_ms": 1.0,
        "data": base64.b64encode(pcm).decode("ascii"),
    }


@pytest.mark.asyncio
async def test_audio_chunk_dropped_while_agent_turn_running(monkeypatch):
    async def _no_finalize(*_args, **_kwargs):
        return False

    monkeypatch.setattr(app_ws, "_finalize_turn_if_ready", _no_finalize)
    sess = app_ws._Session("guest")
    task = asyncio.create_task(asyncio.sleep(10))
    sess.active_turn_task = task
    try:
        assert await app_ws._ingest_frame(_Ws(), sess, _audio_frame()) is True
        assert bytes(sess.audio_buf) == b""
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_audio_chunk_accepted_after_agent_turn_finishes(monkeypatch):
    async def _no_finalize(*_args, **_kwargs):
        return False

    monkeypatch.setattr(app_ws, "_finalize_turn_if_ready", _no_finalize)
    sess = app_ws._Session("guest")
    task = asyncio.create_task(asyncio.sleep(0))
    await task
    sess.active_turn_task = task

    assert await app_ws._ingest_frame(_Ws(), sess, _audio_frame()) is True
    assert len(sess.audio_buf) > 0
