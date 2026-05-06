"""Standalone smoke test for Deepgram Nova-2 ASR.

Usage:
    python -m server.test_deepgram_smoke /path/to/file.wav

Prints the transcribed text. Exits non-zero on empty/error.
"""
from __future__ import annotations

import sys

from server import config, deepgram_asr


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m server.test_deepgram_smoke <wav_path>", file=sys.stderr)
        return 2

    wav_path = sys.argv[1]
    print("USE_DEEPGRAM={0} model={1} lang={2}".format(
        config.USE_DEEPGRAM, config.DEEPGRAM_MODEL, config.DEEPGRAM_LANGUAGE,
    ))
    print("api_key_set={0}".format(bool(config.DEEPGRAM_API_KEY)))
    text = deepgram_asr.transcribe(wav_path)
    print("---")
    print(text or "<EMPTY>")
    print("---")
    return 0 if text else 1


if __name__ == "__main__":
    sys.exit(main())
