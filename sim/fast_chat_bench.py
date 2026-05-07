# -*- coding: utf-8 -*-
"""End-to-end fast-chat lane benchmark.

Measures the FULL chat turn after the ElevenLabs TTS switch:

  STT (Whisper or Deepgram)  → transcript
  Model (gpt-4.1-nano)       → first-token / done
  Sentence chunker           → first complete sentence
  TTS (ElevenLabs Flash)     → first audio bytes
  ─────────────────────────────────────────
  total first-audio          (mouth-close → speaker-start)
  total turn                 (mouth-close → reply complete)

Synthesizes the user prompt as audio (OpenAI tts-1 nova) so the bench
is deterministic and we have a real WAV to feed the STT step.

Usage:
    set -a; source .env; set +a
    python -m sim.fast_chat_bench
    python -m sim.fast_chat_bench --runs 3
    python -m sim.fast_chat_bench --tts-provider auto|elevenlabs|openai
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

REPORTS = Path(__file__).resolve().parent / "reports" / "fast_chat_bench"

PROMPTS = [
    "hi nao what's up",
    "tell me a joke",
    "what's your favorite color",
    "say something nice",
    "do you like dancing",
]


@dataclasses.dataclass
class Run:
    prompt: str
    stt_provider: str
    stt_ms: float
    transcript: str
    model_first_token_ms: float
    model_done_ms: float
    tts_provider: str
    first_sentence_ready_ms: float       # tokens-arriving + chunker boundary
    tts_first_audio_ms: float            # synth(first_sentence) duration
    total_first_audio_ms: float          # turn-start → first audio bytes
    total_turn_ms: float                 # turn-start → reply complete
    reply: str


def _synth_user_audio(text: str, out_wav: Path) -> bool:
    """OpenAI TTS the prompt → MP3 → 16 kHz mono WAV (Whisper input)."""
    from server import openai_tts
    mp3 = openai_tts.synthesize(text)
    if not mp3:
        return False
    mp3p = out_wav.with_suffix(".mp3")
    mp3p.write_bytes(mp3)
    rc = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(mp3p), "-ac", "1", "-ar", "16000", str(out_wav),
    ], capture_output=True).returncode
    try: mp3p.unlink()
    except Exception: pass
    return rc == 0


def _stt(wav_path: Path) -> tuple[str, float, str]:
    """Pick the production STT path: Deepgram if enabled, else Whisper."""
    from server import config
    if getattr(config, "USE_DEEPGRAM", False) and getattr(config, "DEEPGRAM_API_KEY", ""):
        from server import deepgram_asr
        t = time.perf_counter()
        text = deepgram_asr.transcribe(str(wav_path)) or ""
        return text, (time.perf_counter() - t) * 1000.0, "deepgram"
    from openai import OpenAI
    t = time.perf_counter()
    with open(wav_path, "rb") as f:
        resp = OpenAI().audio.transcriptions.create(
            model=config.WHISPER_MODEL, file=f,
            language="en", temperature=0,
        )
    return (resp.text or ""), (time.perf_counter() - t) * 1000.0, "whisper"


def _pick_synth(provider_pref: str) -> tuple[Callable[[str], bytes | None], str]:
    """Return (synth_fn, provider_name) per the user's preference."""
    from server import openai_tts
    try:
        from server import elevenlabs_tts
        eleven_ok = elevenlabs_tts.is_available()
    except Exception:
        elevenlabs_tts = None  # type: ignore
        eleven_ok = False

    if provider_pref == "openai" or not eleven_ok:
        return openai_tts.synthesize, "openai"
    if provider_pref == "elevenlabs":
        return elevenlabs_tts.synthesize, "elevenlabs"  # type: ignore[union-attr]
    # auto: prefer EL when available
    return elevenlabs_tts.synthesize, "elevenlabs"  # type: ignore[union-attr]


async def run_one(prompt: str, *, runs_dir: Path,
                   tts_provider_pref: str) -> Run:
    from server import _legacy_helpers as legacy
    from server.streaming import chunk_for_tts

    # 1. Synth user prompt audio (this isn't part of the timed path —
    #    it's our "user voice" stand-in)
    wav = runs_dir / (prompt.replace(" ", "_")[:30] + ".wav")
    if not wav.exists():
        if not _synth_user_audio(prompt, wav):
            raise RuntimeError(f"failed to synth user audio for {prompt!r}")

    # ── Timer starts here: simulates user-mouth-close ──────────────
    t_user_done = time.perf_counter()

    # 2. STT
    transcript, stt_ms, stt_provider = _stt(wav)

    # 3. Stream the chat agent. Pass image=None and vision=None — fast
    #    chat lane skips vision per Phase 11.7.
    delta_q: asyncio.Queue[str | None] = asyncio.Queue()
    full = ""
    t_first_token: float | None = None
    t_done: float | None = None

    async def deltas():
        while True:
            x = await delta_q.get()
            if x is None: return
            yield x

    async def drive():
        nonlocal full, t_first_token, t_done
        async for ev in legacy.run_agent_streamed(
                "fast_bench", "chat", transcript, None, None):
            if ev["type"] == "delta":
                if t_first_token is None:
                    t_first_token = time.perf_counter()
                full += ev["text"]
                await delta_q.put(ev["text"])
            elif ev["type"] == "done":
                t_done = time.perf_counter()
        await delta_q.put(None)

    drive_task = asyncio.create_task(drive())

    # 4. TTS the first sentence as soon as it lands
    synth_fn, tts_provider = _pick_synth(tts_provider_pref)

    t_first_audio: float | None = None
    t_first_sentence: float | None = None
    async for sent in chunk_for_tts(deltas()):
        if t_first_audio is None:
            t_first_sentence = time.perf_counter()
            mp3 = await asyncio.to_thread(synth_fn, sent)
            t_first_audio = time.perf_counter()
        else:
            await asyncio.to_thread(synth_fn, sent)
    await drive_task

    t_complete = time.perf_counter()

    return Run(
        prompt=prompt,
        stt_provider=stt_provider,
        stt_ms=round(stt_ms, 1),
        transcript=transcript,
        model_first_token_ms=round(
            ((t_first_token or t_user_done) - t_user_done - stt_ms / 1000.0) * 1000.0, 1
        ),
        model_done_ms=round(
            ((t_done or t_user_done) - t_user_done - stt_ms / 1000.0) * 1000.0, 1
        ),
        tts_provider=tts_provider,
        first_sentence_ready_ms=round(
            ((t_first_sentence or t_user_done) - t_user_done) * 1000.0, 1
        ),
        tts_first_audio_ms=round(
            ((t_first_audio or t_user_done) - (t_first_sentence or t_user_done)) * 1000.0, 1
        ),
        total_first_audio_ms=round(
            ((t_first_audio or t_user_done) - t_user_done) * 1000.0, 1
        ),
        total_turn_ms=round((t_complete - t_user_done) * 1000.0, 1),
        reply=full.strip(),
    )


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--runs", type=int, default=1, help="repeats per prompt")
    p.add_argument("--tts-provider", default="auto",
                   choices=["auto", "openai", "elevenlabs"])
    p.add_argument("--prompts", default="")
    args = p.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY required", file=sys.stderr); return 2

    prompts = (
        [p.strip() for p in args.prompts.split(";") if p.strip()]
        or PROMPTS
    )

    import datetime as _dt
    stamp = _dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    run_dir = REPORTS / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== Fast-chat full-pipeline bench @ {stamp} ===")
    print(f"    tts-provider: {args.tts_provider}  runs/prompt: {args.runs}")
    print()

    rows: list[Run] = []
    for prompt in prompts:
        for i in range(args.runs):
            try:
                r = await run_one(prompt, runs_dir=run_dir,
                                  tts_provider_pref=args.tts_provider)
                rows.append(r)
                print(f"  [{r.tts_provider:<10}] {r.prompt!r:<32}  "
                      f"stt={r.stt_ms:>5.0f}  model={r.model_done_ms:>5.0f}  "
                      f"tts={r.tts_first_audio_ms:>5.0f}  "
                      f"first_audio={r.total_first_audio_ms:>5.0f}  "
                      f"total={r.total_turn_ms:>5.0f}")
            except Exception as e:
                print(f"  FAIL {prompt!r}: {e!r}")

    # Aggregate
    print()
    print("=" * 86)
    print(f"{'col':<22}{'min':>8}{'p50':>8}{'p95':>8}{'max':>8}{'mean':>8}")
    print(f"{'---':<22}{'---':>8}{'---':>8}{'---':>8}{'---':>8}{'----':>8}")

    def _stats(label: str, values: list[float]):
        values = [v for v in values if v is not None and v >= 0]
        if not values:
            return
        values_sorted = sorted(values)
        mean = sum(values) / len(values)
        p50 = statistics.median(values)
        p95 = values_sorted[max(0, int(len(values) * 0.95) - 1)]
        print(f"{label:<22}{min(values):>8.0f}{p50:>8.0f}"
              f"{p95:>8.0f}{max(values):>8.0f}{mean:>8.0f}")

    _stats("stt_ms",                [r.stt_ms for r in rows])
    _stats("model_first_token_ms",  [r.model_first_token_ms for r in rows])
    _stats("model_done_ms",         [r.model_done_ms for r in rows])
    _stats("tts_first_audio_ms",    [r.tts_first_audio_ms for r in rows])
    _stats("first_sentence_ready",  [r.first_sentence_ready_ms for r in rows])
    _stats("TOTAL_first_audio_ms",  [r.total_first_audio_ms for r in rows])
    _stats("TOTAL_turn_ms",         [r.total_turn_ms for r in rows])

    # Persist
    import json
    (run_dir / "results.json").write_text(
        json.dumps([dataclasses.asdict(r) for r in rows], indent=2),
        encoding="utf-8",
    )
    print()
    print(f"json: {run_dir/'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
