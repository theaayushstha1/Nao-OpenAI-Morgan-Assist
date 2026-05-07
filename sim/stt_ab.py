# -*- coding: utf-8 -*-
"""STT A/B benchmark — OpenAI Whisper vs Deepgram vs ElevenLabs Scribe.

Generates a fixed clip set (synthesizes phrases via OpenAI TTS for
deterministic ground truth), optionally adds noise / echo variants,
then transcribes each clip through every available provider and
reports per-clip accuracy + latency.

Usage:
    set -a; source .env; set +a
    python -m sim.stt_ab
    python -m sim.stt_ab --providers openai,deepgram   # subset
    python -m sim.stt_ab --variants clean,noisy,echo   # which clip variants

Outputs:
    sim/reports/stt_ab/<stamp>/clips/*.wav         — generated test clips
    sim/reports/stt_ab/<stamp>/results.json        — full data
    sim/reports/stt_ab/<stamp>/results.md          — human-readable
    sim/reports/stt_ab/<stamp>/summary.csv         — for spreadsheets

Honest limitations
------------------
- Test clips are TTS-generated, so they represent the OpenAI ``nova``
  voice in clean conditions. They do NOT cover real-world cases like:
    • Asian/Nepali accent specifically
    • Real NAO mic SNR
    • Real speaker echo (we synthesize a coarse echo via ffmpeg, not
      acoustic)
  For production decision, supplement with real recordings from the
  robot. This bench is the FRAMEWORK; feed it actual NAO audio later.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as _dt
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

REPORTS_DIR = Path(__file__).resolve().parent / "reports" / "stt_ab"

# ──────────────────────────────────────────────────────────────────────────
# Test phrases — the listed NAO problem cases
# ──────────────────────────────────────────────────────────────────────────

TEST_PHRASES: list[tuple[str, str]] = [
    # (slug, ground_truth_transcript)
    ("short_chat",       "chat"),
    ("short_morgan",     "morgan"),
    ("short_therapy",    "therapy"),
    ("name_aayush",      "my name is Aayush"),
    ("name_shrestha",    "I'm Aayush Shrestha"),
    ("course_cosc490",   "what is COSC 490"),
    ("course_cosc111",   "tell me about COSC 111"),
    ("therapy_anxious",  "I'm feeling really anxious about my midterms"),
    ("therapy_panic",    "I think I'm having a panic attack right now"),
    ("therapy_alone",    "I feel completely alone today"),
    ("greeting",         "hi nao what's up"),
    ("question_capstone","what is the senior capstone course"),
    ("safe_short",       "thanks"),
    ("complex_sentence", "I want to declare a computer science major before the end of the semester"),
]

# ──────────────────────────────────────────────────────────────────────────
# Variants — clean / noisy / echo — generated via ffmpeg
# ──────────────────────────────────────────────────────────────────────────

VARIANTS: dict[str, str] = {
    # Clean is just the raw TTS output.
    "clean": "",
    # Noisy: pink noise mixed at -22dB. Approximates a busy classroom.
    "noisy": (
        "[0:a]asplit=2[main][mix];"
        "anoisesrc=color=pink:amplitude=0.06:duration=8[n];"
        "[mix][n]amix=inputs=2:duration=first:dropout_transition=0,"
        "volume=1.5[s];"
        "[main]anull"
    ),
    # Echo: simulates a hard acoustic with a 250 ms delay tap at 0.4
    # gain. Coarse approximation of NAO speaker reflecting off a wall
    # back into the mic.
    "echo": "aecho=0.8:0.9:250:0.4",
}


# ──────────────────────────────────────────────────────────────────────────
# Result types
# ──────────────────────────────────────────────────────────────────────────


@dataclasses.dataclass
class ClipResult:
    provider: str
    slug: str
    variant: str
    ground_truth: str
    transcript: str
    elapsed_ms: float
    error: str | None = None


def _normalize_text(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. WER-friendly."""
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _word_error_rate(ref: str, hyp: str) -> float:
    """Standard WER (Levenshtein on token sequences). 0.0 = perfect."""
    r = _normalize_text(ref).split()
    h = _normalize_text(hyp).split()
    if not r:
        return 0.0 if not h else 1.0
    # Tiny DP — these strings are short.
    rows = len(r) + 1
    cols = len(h) + 1
    d = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        d[i][0] = i
    for j in range(cols):
        d[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if r[i - 1] == h[j - 1] else 1
            d[i][j] = min(
                d[i - 1][j] + 1,        # del
                d[i][j - 1] + 1,        # ins
                d[i - 1][j - 1] + cost, # sub
            )
    return d[-1][-1] / max(1, len(r))


def _exact_match(ref: str, hyp: str) -> bool:
    return _normalize_text(ref) == _normalize_text(hyp)


# ──────────────────────────────────────────────────────────────────────────
# Clip generation
# ──────────────────────────────────────────────────────────────────────────


def _ffmpeg_available() -> bool:
    return subprocess.run(
        ["ffmpeg", "-version"], capture_output=True
    ).returncode == 0


def _synthesize_clip(text: str, out_wav: Path) -> bool:
    """Use OpenAI TTS to produce a deterministic 16kHz mono WAV."""
    from server import openai_tts
    mp3 = openai_tts.synthesize(text)
    if not mp3:
        return False
    mp3_path = out_wav.with_suffix(".mp3")
    mp3_path.write_bytes(mp3)
    # Convert MP3 → 16kHz mono PCM16 WAV (Whisper / Deepgram / EL all
    # accept this directly).
    rc = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(mp3_path),
        "-ac", "1", "-ar", "16000",
        str(out_wav),
    ], capture_output=True).returncode
    try:
        mp3_path.unlink()
    except Exception:
        pass
    return rc == 0


def _make_variant(src: Path, dst: Path, variant: str) -> bool:
    if variant == "clean":
        return src.resolve() == dst.resolve() or _copy(src, dst)
    f = VARIANTS.get(variant)
    if not f:
        return False
    if "[" in f:
        # complex filtergraph — use -filter_complex
        rc = subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(src),
            "-filter_complex", f,
            "-map", "[s]",
            "-ac", "1", "-ar", "16000",
            str(dst),
        ], capture_output=True).returncode
    else:
        rc = subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(src),
            "-af", f,
            "-ac", "1", "-ar", "16000",
            str(dst),
        ], capture_output=True).returncode
    return rc == 0


def _copy(src: Path, dst: Path) -> bool:
    try:
        dst.write_bytes(src.read_bytes())
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────
# Provider adapters — each returns (transcript, elapsed_ms)
# ──────────────────────────────────────────────────────────────────────────


def _provider_openai(wav: Path) -> tuple[str, float]:
    from server import config
    from openai import OpenAI
    client = OpenAI()
    t0 = time.perf_counter()
    with open(wav, "rb") as f:
        resp = client.audio.transcriptions.create(
            model=config.WHISPER_MODEL, file=f,
            language="en", temperature=0,
        )
    dt = (time.perf_counter() - t0) * 1000.0
    return (resp.text or ""), dt


def _provider_deepgram(wav: Path) -> tuple[str, float]:
    from server import deepgram_asr
    t0 = time.perf_counter()
    text = deepgram_asr.transcribe(str(wav))
    dt = (time.perf_counter() - t0) * 1000.0
    return text or "", dt


def _provider_elevenlabs(wav: Path) -> tuple[str, float]:
    from server import elevenlabs_stt
    if not elevenlabs_stt.is_available():
        return "", 0.0
    t0 = time.perf_counter()
    text = elevenlabs_stt.transcribe_file(str(wav))
    dt = (time.perf_counter() - t0) * 1000.0
    return text or "", dt


PROVIDERS: dict[str, Callable[[Path], tuple[str, float]]] = {
    "openai": _provider_openai,
    "deepgram": _provider_deepgram,
    "elevenlabs": _provider_elevenlabs,
}


# ──────────────────────────────────────────────────────────────────────────
# Driver
# ──────────────────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(description="STT A/B benchmark")
    p.add_argument("--providers", default="openai,deepgram,elevenlabs")
    p.add_argument("--variants", default="clean,noisy,echo")
    p.add_argument("--phrases", default="",
                   help="comma-separated slugs to filter (default: all)")
    p.add_argument("--keep-clips", action="store_true",
                   help="leave generated WAVs on disk")
    args = p.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY required to synth test clips",
              file=sys.stderr)
        return 2
    if not _ffmpeg_available():
        print("ERROR: ffmpeg required for clip variants", file=sys.stderr)
        return 2

    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    phrase_filter = {p.strip() for p in args.phrases.split(",") if p.strip()}
    selected_phrases = [
        (slug, txt) for slug, txt in TEST_PHRASES
        if not phrase_filter or slug in phrase_filter
    ]

    # Skip providers that aren't usable in this env.
    usable: list[str] = []
    for name in providers:
        if name == "elevenlabs":
            try:
                from server import elevenlabs_stt
                if not elevenlabs_stt.is_available():
                    print(f"  • {name}: skipping (not configured — set "
                          f"USE_ELEVENLABS_STT=1 + ELEVENLABS_API_KEY)")
                    continue
            except Exception:
                continue
        if name == "deepgram":
            from server import config
            if not getattr(config, "DEEPGRAM_API_KEY", "") or \
               not getattr(config, "USE_DEEPGRAM", False):
                print(f"  • {name}: skipping (USE_DEEPGRAM=0 or no key)")
                continue
        if name not in PROVIDERS:
            print(f"  • {name}: unknown provider; skipping")
            continue
        usable.append(name)

    if not usable:
        print("No usable providers selected. Exiting.")
        return 2

    print(f"Providers: {', '.join(usable)}")
    print(f"Variants:  {', '.join(variants)}")
    print(f"Phrases:   {len(selected_phrases)}")
    print()

    stamp = _dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    run_dir = REPORTS_DIR / stamp
    clips_dir = run_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    print("[1/2] generating clips ...")
    clip_paths: dict[tuple[str, str], Path] = {}
    for slug, text in selected_phrases:
        clean_path = clips_dir / f"{slug}__clean.wav"
        if not clean_path.exists():
            ok = _synthesize_clip(text, clean_path)
            if not ok:
                print(f"  ✗ {slug}: synth failed")
                continue
        clip_paths[(slug, "clean")] = clean_path
        for v in variants:
            if v == "clean":
                continue
            vpath = clips_dir / f"{slug}__{v}.wav"
            if not vpath.exists():
                if _make_variant(clean_path, vpath, v):
                    clip_paths[(slug, v)] = vpath
                else:
                    print(f"  ✗ {slug}/{v}: variant failed")
            else:
                clip_paths[(slug, v)] = vpath
    print(f"  → {len(clip_paths)} clips ready")
    print()

    print("[2/2] transcribing ...")
    results: list[ClipResult] = []
    for (slug, variant), wav in clip_paths.items():
        gt = next(t for s, t in selected_phrases if s == slug)
        for pname in usable:
            try:
                txt, dt = PROVIDERS[pname](wav)
                err = None
            except Exception as e:  # noqa: BLE001
                txt, dt, err = "", 0.0, repr(e)
            results.append(ClipResult(
                provider=pname, slug=slug, variant=variant,
                ground_truth=gt, transcript=txt,
                elapsed_ms=round(dt, 1), error=err,
            ))
            short = (txt or err or "")[:60].replace("\n", " ")
            wer = _word_error_rate(gt, txt)
            ok = "✓" if _exact_match(gt, txt) else "·"
            print(f"  {ok} [{pname:>10}] {slug:<22} {variant:<6}  "
                   f"{dt:>6.0f} ms  WER={wer:.2f}  → {short!r}")
    print()

    # Persist
    json_path = run_dir / "results.json"
    md_path = run_dir / "results.md"
    csv_path = run_dir / "summary.csv"

    json_path.write_text(json.dumps(
        {"generated_at": stamp,
         "providers": usable, "variants": variants,
         "phrases": [{"slug": s, "text": t} for s, t in selected_phrases],
         "results": [dataclasses.asdict(r) for r in results]},
        indent=2,
    ), encoding="utf-8")

    # Per-provider aggregate
    by_prov: dict[str, dict] = {}
    for r in results:
        agg = by_prov.setdefault(r.provider, {
            "n": 0, "exact": 0, "wer_sum": 0.0, "ms_sum": 0.0,
            "errors": 0,
        })
        agg["n"] += 1
        if r.error or not r.transcript:
            agg["errors"] += 1
            continue
        agg["wer_sum"] += _word_error_rate(r.ground_truth, r.transcript)
        agg["ms_sum"] += r.elapsed_ms
        if _exact_match(r.ground_truth, r.transcript):
            agg["exact"] += 1

    md = ["# STT A/B benchmark", "",
          f"> Generated: `{stamp}`",
          f"> Providers: {', '.join(usable)}",
          f"> Variants:  {', '.join(variants)}",
          f"> Clips:     {len(clip_paths)}",
          ""]
    md.append("## Aggregate")
    md.append("")
    md.append("| Provider | N | Exact match | Mean WER | Mean latency | Errors |")
    md.append("|---|---|---|---|---|---|")
    for prov, a in by_prov.items():
        valid = max(1, a["n"] - a["errors"])
        md.append(
            f"| `{prov}` | {a['n']} | {a['exact']}/{a['n']} "
            f"({a['exact']*100/max(1, a['n']):.0f}%) "
            f"| {a['wer_sum']/valid:.2f} | {a['ms_sum']/valid:.0f} ms "
            f"| {a['errors']} |"
        )
    md.append("")
    md.append("## Per-clip results")
    md.append("")
    md.append("| Provider | Slug | Variant | Ground truth | Transcript | WER | Latency |")
    md.append("|---|---|---|---|---|---|---|")
    for r in results:
        wer = _word_error_rate(r.ground_truth, r.transcript)
        gt = r.ground_truth.replace("|", "\\|")
        ts = (r.transcript or r.error or "").replace("|", "\\|")
        md.append(
            f"| `{r.provider}` | `{r.slug}` | `{r.variant}` "
            f"| {gt} | {ts} | {wer:.2f} | {r.elapsed_ms:.0f} ms |"
        )
    md_path.write_text("\n".join(md), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["provider", "slug", "variant", "ground_truth",
                     "transcript", "elapsed_ms", "wer", "exact_match",
                     "error"])
        for r in results:
            w.writerow([
                r.provider, r.slug, r.variant, r.ground_truth,
                r.transcript, r.elapsed_ms,
                f"{_word_error_rate(r.ground_truth, r.transcript):.3f}",
                str(_exact_match(r.ground_truth, r.transcript)).lower(),
                r.error or "",
            ])

    print("=" * 78)
    print("AGGREGATE")
    print("=" * 78)
    print(f"  {'provider':<14}{'N':>4}{'exact':>10}{'mean WER':>12}"
          f"{'mean ms':>12}{'errors':>10}")
    for prov, a in by_prov.items():
        valid = max(1, a["n"] - a["errors"])
        print(f"  {prov:<14}{a['n']:>4}"
              f"{a['exact']:>5}/{a['n']:<4}"
              f"{a['wer_sum']/valid:>11.2f}"
              f"{a['ms_sum']/valid:>11.0f}"
              f"{a['errors']:>10}")
    print()
    print(f"json: {json_path}")
    print(f"md:   {md_path}")
    print(f"csv:  {csv_path}")
    if not args.keep_clips:
        print(f"clips kept at {clips_dir}  (--keep-clips for explicit retention note)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
