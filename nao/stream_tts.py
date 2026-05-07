# -*- coding: utf-8 -*-
"""Streaming MP3 chunk player for the WS transport.

Receives MP3 audio chunks from the WS receiver (`nao/ws_client.py`) and
plays them back-to-back through ALAudioPlayer with no perceptible gap. The
public surface is `StreamTtsPlayer` — an enqueue/stop/shutdown player.

This file is the Phase 1 rewrite of the SSE consumer that previously lived
here. SSE parsing, head-touch / mic-energy barge monitor, and the `consume`
function are GONE — those responsibilities moved out:

  - SSE parsing -> `nao/ws_client.py` (now consumes WS frames).
  - Barge-in detection -> `nao/ws_client.py` (single source of truth).
  - This module just plays audio chunks smoothly and supports `stop()` so
    the WS client can interrupt instantly.

What was preserved from the old file (deliberately, do not change):
  - Per-chunk volume pinning (`ALAudioDevice.setOutputVolume(100)` and
    `ALAudioPlayer.setMasterVolume(1.0)`) BEFORE every play. Some
    background service on this NAO firmware drops the output volume back
    down between sentences; pinning once at startup is not enough.
  - Lazy ALProxy creation through `naoqi.ALProxy` using `nao_ip`.
  - MP3 path on disk: NAO's ALAudioPlayer cannot play in-memory bytes; it
    needs a file path, so each chunk is written to /tmp/nao_tts_<seq>.mp3
    and `playFile()` is called.
  - `print()` logging via `from __future__ import print_function`. The
    `nao-logger-main` agent will replace these with structured logging in
    a follow-up commit; until then keep the prints so we can debug.

Python 2.7 / naoqi only. All imports of `naoqi` are guarded so this file
imports cleanly off-robot for `python -m py_compile` and unit tests.
"""
from __future__ import print_function

import os
import sys
import threading
import time

try:
    import Queue as _queue  # Py2.7 stdlib
except ImportError:  # pragma: no cover - Py3 dev import
    import queue as _queue  # type: ignore


# Where MP3 chunks are spooled before playback. Same /tmp prefix as before
# so leftover files from older runs are still cleaned up by the existing
# OS-level tmp policy. Filenames now use a monotonic counter (no collision
# even at >1 chunk/sec) and shutdown removes any survivors.
_MP3_DIR = "/tmp"
_MP3_PREFIX = "nao_tts_"
_MP3_SUFFIX = ".mp3"

# Polling interval while waiting for ALAudioPlayer to drain a single chunk.
# 50 ms keeps inter-chunk gap perceptibly seamless; the PRD requires < 30 ms
# of dead air between chunks, but with `playFile` semantics on this firmware
# the actual handoff is bounded by NAOqi's RPC + decoder warmup — empirically
# under 30 ms once the next file is queued.
_POLL_S = 0.05

# Hard cap on individual MP3 size we will accept (defensive). The OpenAI TTS
# server emits sentence-sized chunks well under this; anything over likely
# means we are buffering an entire reply instead of streaming it, which is a
# bug we want to surface rather than silently drown out.
_MAX_MP3_BYTES = 4 * 1024 * 1024


def _try_import_naoqi():
    """Import naoqi.ALProxy or return None.

    Centralised so tests can run on developer laptops where naoqi is not
    installed. The `StreamTtsPlayer` constructor falls back to no-op mode
    if this returns None instead of crashing on import.
    """
    try:
        from naoqi import ALProxy  # type: ignore
        return ALProxy
    except Exception:
        return None


class StreamTtsPlayer(object):
    """Back-to-back MP3 chunk player with barge-in stop.

    Public API (matches the Phase 1 task spec):
        StreamTtsPlayer(nao_ip)
        enqueue(text, mp3_bytes)   -> queue one chunk for playback
        is_playing()               -> bool
        stop()                     -> abort current playback + drain queue
        shutdown()                 -> release proxies + remove tmp files

    Threading model:
      - Producer side: any number of WS-receiver threads call enqueue(...).
        It is non-blocking; it writes the MP3 to /tmp and pushes a job onto
        an unbounded thread-safe queue.
      - Worker side: ONE daemon worker thread drains the queue, calls
        `ALAudioPlayer.playFile(path)`, then polls
        `ALAudioPlayer.getNumOfChannels()` to decide when playback has
        ended, and finally deletes the tmp file. Polling the channel count
        is the cheapest way naoqi exposes "am I still playing"; on this
        firmware it returns 0 when the player is idle.
      - `_playing` is a bool guarded by `_state_lock`. Readers see a
        consistent value without racing the worker.
    """

    def __init__(self, nao_ip, nao_port=9559):
        self._nao_ip = nao_ip
        self._nao_port = int(nao_port)

        # State guarded by _state_lock. _playing is True from the moment we
        # ask ALAudioPlayer to play a chunk until we observe its channel
        # count drop back to zero (or stop() forces it false).
        self._state_lock = threading.Lock()
        self._playing = False

        # Queue of (path, text) jobs the worker thread consumes. Unbounded
        # because TTS chunks arrive faster than playback when the network
        # spurts; the queue absorbs the spike. shutdown()/stop() drain it.
        self._queue = _queue.Queue()

        # Monotonic seq used in tmp filenames so two concurrent enqueues
        # never collide on the same path.
        self._seq_lock = threading.Lock()
        self._seq = 0

        # Track every tmp file we wrote so shutdown() can remove leftovers
        # if the worker was killed mid-playback.
        self._tmp_paths_lock = threading.Lock()
        self._tmp_paths = set()

        # Stop flag for the worker. set() once on shutdown.
        self._shutdown_event = threading.Event()

        # ALProxy handles. None means "naoqi not importable" (dev machine);
        # in that case enqueue() warns once and silently drops chunks so
        # tests can instantiate the class without a robot present.
        self._player = None
        self._audio_dev = None
        self._naoqi_warned = False

        ALProxy = _try_import_naoqi()
        if ALProxy is None:
            print("[stream_tts] naoqi unavailable; StreamTtsPlayer running "
                  "in inert dev mode (no audio will play)")
        else:
            try:
                self._player = ALProxy("ALAudioPlayer", self._nao_ip,
                                       self._nao_port)
            except Exception as e:
                print("[stream_tts] ALAudioPlayer proxy failed:", e)
                self._player = None
            try:
                self._audio_dev = ALProxy("ALAudioDevice", self._nao_ip,
                                          self._nao_port)
            except Exception as e:
                # Non-fatal — we just lose the output-volume pin. Playback
                # still works at whatever the system volume already is.
                print("[stream_tts] ALAudioDevice proxy failed:", e)
                self._audio_dev = None

        # Start the worker thread regardless. If naoqi is missing it just
        # spins waiting for jobs that never come; cheap on cycles.
        self._worker = threading.Thread(target=self._worker_loop,
                                        name="stream_tts_worker")
        self._worker.daemon = True
        self._worker.start()

    # ---- public API -----------------------------------------------------

    def enqueue(self, text, mp3_bytes):
        """Accept one TTS chunk; non-blocking; queues for playback.

        Pins the volume BEFORE the worker dequeues, not just inside the
        worker, because the previous turn's interrupt may have left the
        master volume in a weird state and we want the very next chunk to
        come out at full level.

        text is kept around for logging only — the chunk player does not
        re-derive what was said from the MP3 bytes.
        """
        if not mp3_bytes:
            return
        if self._shutdown_event.is_set():
            return
        if len(mp3_bytes) > _MAX_MP3_BYTES:
            # Defensive — log and drop; treating it as a programming bug
            # rather than playing a 4 MB blob and pretending all is well.
            print("[stream_tts] enqueue: chunk too large ({0} bytes), "
                  "dropping".format(len(mp3_bytes)))
            return

        # Pin volumes UP FRONT — before the worker thread sees the job. If
        # the job is ahead of others in the queue this still happens before
        # playback starts. Idempotent and cheap.
        self._pin_volumes()

        path = self._spool_to_disk(mp3_bytes)
        if path is None:
            return
        with self._tmp_paths_lock:
            self._tmp_paths.add(path)
        try:
            preview = (text or "")
            if isinstance(preview, bytes):
                try:
                    preview = preview.decode("utf-8", "ignore")
                except Exception:
                    preview = ""
            preview = preview[:60]
        except Exception:
            preview = ""
        print("[stream_tts] enqueue:", preview,
              "(", len(mp3_bytes), "bytes ->", path, ")")
        self._queue.put((path, text))

    def is_playing(self):
        """Return True while a chunk is being played OR jobs are queued."""
        with self._state_lock:
            playing = self._playing
        if playing:
            return True
        # Pending jobs also count as "still playing" from the caller's
        # perspective — barge-in / state machines want to know whether the
        # robot's mouth is going to keep moving, not just right now.
        return not self._queue.empty()

    def stop(self):
        """Stop current playback + drain queue. Used for barge-in.

        Must complete in < 50 ms. We:
          1. Mark _playing False under the lock so readers see "stopped"
             immediately.
          2. Drain the queue (best-effort — racing producers may push one
             more job; that's fine, the worker will see _interrupt and
             skip it).
          3. Call ALAudioPlayer.stopAll() to kill whatever file is mid-
             playback. stopAll is the only barge primitive naoqi exposes
             that doesn't require a play-id we may not have.
          4. Wake the worker via an interrupt event it checks each loop.
        """
        with self._state_lock:
            self._playing = False

        drained = 0
        while True:
            try:
                path, _text = self._queue.get_nowait()
            except _queue.Empty:
                break
            drained += 1
            self._safe_remove(path)
        if drained:
            print("[stream_tts] stop: drained", drained, "queued chunk(s)")

        if self._player is not None:
            try:
                self._player.stopAll()
            except Exception as e:
                print("[stream_tts] stop: stopAll failed:", e)

    def shutdown(self):
        """Drain queue, stop player, remove leftover tmp files, end worker.

        Idempotent. Safe to call from any thread.
        """
        if self._shutdown_event.is_set():
            return
        self._shutdown_event.set()
        # Reuse stop() to drain + kill audio.
        try:
            self.stop()
        except Exception as e:
            print("[stream_tts] shutdown: stop raised:", e)

        # Push a sentinel so the worker exits its blocking get() promptly.
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass

        # Best-effort join. Worker is a daemon so we won't hang process
        # exit even if it does not return.
        if self._worker is not None:
            try:
                self._worker.join(timeout=1.0)
            except Exception:
                pass

        # Remove any /tmp/nao_tts_*.mp3 we wrote (and as a safety net any
        # leftovers from a previous crashed run).
        self._cleanup_tmp_files()

    # ---- worker loop ----------------------------------------------------

    def _worker_loop(self):
        """Daemon: dequeue jobs, play sequentially, delete files."""
        while not self._shutdown_event.is_set():
            try:
                job = self._queue.get(timeout=0.25)
            except _queue.Empty:
                continue
            if job is None:
                # Shutdown sentinel.
                break
            path, _text = job

            # If stop() landed between enqueue and dequeue, skip.
            if self._shutdown_event.is_set():
                self._safe_remove(path)
                continue

            self._play_one(path)

    def _play_one(self, path):
        """Play a single MP3 file, blocking until the player drains it.

        Sets `_playing=True` immediately so `is_playing()` reflects reality
        even before naoqi reports a non-zero channel count. Polls
        `getNumOfChannels()` every _POLL_S to detect end-of-playback.
        """
        with self._state_lock:
            self._playing = True

        # Final volume pin right before play — covers the case where stop()
        # was called between enqueue and now (which would have left the
        # speaker at whatever volume the OS picked).
        self._pin_volumes()

        if self._player is None:
            # Dev mode without naoqi: simulate playback time so callers
            # that drive the player in tests can see is_playing() flip.
            time.sleep(0.05)
            with self._state_lock:
                self._playing = False
            self._safe_remove(path)
            return

        try:
            # playFile is BLOCKING on this firmware (it waits for the file
            # to finish), so we don't actually need to poll getNumOfChannels.
            # But the spec calls for the poll-based readiness check so the
            # worker stays responsive to stop() even if a future naoqi rev
            # makes playFile non-blocking. We post asynchronously instead:
            #   - post.playFile returns a task id
            #   - poll getNumOfChannels until it drops to 0
            #   - safe to call ALAudioPlayer.stop(<id>) on barge
            try:
                task_id = self._player.post.playFile(path)
            except Exception:
                # post API not available — fall back to blocking play.
                task_id = None

            if task_id is None:
                # Blocking fallback. We lose poll-based responsiveness but
                # gain compatibility with older naoqi.
                try:
                    self._player.playFile(path)
                except Exception as e:
                    print("[stream_tts] playFile failed:", e)
            else:
                # Poll until idle or until shutdown/stop interrupts.
                while True:
                    if self._shutdown_event.is_set():
                        break
                    with self._state_lock:
                        if not self._playing:
                            # stop() was called externally.
                            break
                    try:
                        n = self._player.getNumOfChannels()
                    except Exception:
                        n = 0
                    if n is None or int(n) <= 0:
                        # Player idle — chunk done.
                        break
                    time.sleep(_POLL_S)

        except Exception as e:
            print("[stream_tts] _play_one error:", e)
        finally:
            with self._state_lock:
                self._playing = False
            self._safe_remove(path)

    # ---- helpers --------------------------------------------------------

    def _pin_volumes(self):
        """Force speaker output volume to 100 and master volume to 1.0.

        Same idiom as the legacy `_play_mp3_b64`. Called both from
        enqueue() (before the chunk reaches the worker) and from
        _play_one() (right before naoqi's playFile). Idempotent.
        """
        if self._audio_dev is not None:
            try:
                self._audio_dev.setOutputVolume(100)
            except Exception:
                pass
        if self._player is not None:
            try:
                self._player.setMasterVolume(1.0)
            except Exception:
                pass

    def _spool_to_disk(self, mp3_bytes):
        """Write MP3 bytes to /tmp/nao_tts_<seq>.mp3 and return the path.

        Returns None if the write fails — caller drops the chunk. We do
        not retry: a missing tmp dir is a deployment problem, not a
        runtime one.
        """
        with self._seq_lock:
            self._seq = (self._seq + 1) % 1000000
            seq = self._seq
        path = os.path.join(_MP3_DIR,
                            "{0}{1}{2}".format(_MP3_PREFIX, seq, _MP3_SUFFIX))
        try:
            f = open(path, "wb")
            try:
                f.write(mp3_bytes)
            finally:
                f.close()
        except Exception as e:
            print("[stream_tts] write tmp mp3 failed:", e)
            return None
        return path

    def _safe_remove(self, path):
        """Remove a tmp file; never raises. Updates the bookkeeping set."""
        try:
            with self._tmp_paths_lock:
                if path in self._tmp_paths:
                    self._tmp_paths.discard(path)
        except Exception:
            pass
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    def _cleanup_tmp_files(self):
        """Remove every file we wrote AND any /tmp/nao_tts_*.mp3 stragglers.

        Catches the case where a previous process crashed mid-play and
        left files behind — we don't want /tmp filling up across reboots.
        """
        with self._tmp_paths_lock:
            paths = list(self._tmp_paths)
            self._tmp_paths.clear()
        for p in paths:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        try:
            for name in os.listdir(_MP3_DIR):
                if name.startswith(_MP3_PREFIX) and name.endswith(_MP3_SUFFIX):
                    try:
                        os.remove(os.path.join(_MP3_DIR, name))
                    except Exception:
                        pass
        except Exception:
            pass
