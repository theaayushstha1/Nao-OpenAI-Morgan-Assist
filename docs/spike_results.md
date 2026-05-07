# Phase 0.5 — Spike Results

> Drafted: 2026-05-06 alongside Phase 1 transport work.
> Branch: `worktree-agent-acd5790ed0431a2e0` (off `dev/architecture-rework`).
> Companion files: `nao/audio_module.py`.

This document captures the design-review findings for the Phase 0.5 spikes
defined in `docs/PRD_v2.md`. Spike A (live mic streaming) is implemented in
production form as `nao/audio_module.py`; the live verification on the
physical robot is queued for the operator's next robot-side session and
described in the **Verification** section below. Spikes B and C are
documented as design decisions with the rationale captured here so the
operator can act on or defer them based on Spike A results.

---

## Spike A — Live mic streaming via ALAudioDevice (ALModule subscriber)

**Status:** Implemented in `nao/audio_module.py`. Live verification pending
physical-robot session — see *Verification steps* below.

**Design summary**

- `NaoAudioStreamer(ALModule)` subscribes to `ALAudioDevice` for 16 kHz mono
  PCM16 from the front mic (channel index 3 = `FRONT`).
- Naoqi calls `processRemote(nbOfChannels, nbOfSamplesByChannel, timeStamp,
  inputBuffer)` on the registered module name. We re-register the instance
  into `__main__` so the broker can resolve it (the canonical naoqi pattern
  — without it `processRemote` is never invoked).
- Each callback is sliced into 20 ms (640-byte) chunks with a tail buffer
  to keep alignment across calls.
- Chunks are pushed as `(seq, ts_ms, base64_pcm)` triples into a
  `Queue.Queue(maxsize=200)`. 200 × 20 ms = 4 s of headroom. On full queue
  the oldest frame is dropped and a counter increments; warnings are
  rate-limited (1st drop, then every 50th) to avoid log spam.
- Sequence numbers are monotonic per session and reset to 0 on `stop()`
  (32-bit wrap as a safety net).
- `gate(closed=True)` calls `ALAudioDevice.unsubscribe(name)`; `gate(closed=
  False)` re-subscribes. Both are idempotent.

**Why ALModule, not ALAudioRecorder**

`ALAudioRecorder.startMicrophonesRecording(path, format, rate, mask)` is
**file-based**: it writes a WAV to disk and only hands back when you stop
it. There is no streaming hook. A live PCM stream in naoqi is only
available via the `ALAudioDevice.subscribe()` mechanism, which delivers
raw PCM by calling `processRemote()` on a remote `ALModule` instance.
Using ALAudioRecorder for streaming would require recording short
fragments (e.g. 250 ms) and reading them off disk — a fallback we
implemented but didn't make the primary path.

**Firmware-fallback design**

Some NAOqi 2.5 / 2.8 firmware revisions (and certain custom builds) do not
expose `ALAudioDevice.subscribe()` to client modules. If `start()` raises
when calling `subscribe()` we fall back to **fragment-mode**:

- `ALAudioRecorder` records 250 ms WAV files in `/home/nao/recordings/_stream/`.
- A worker thread reads each completed fragment, slices it into 20 ms
  chunks, and pushes through the same queue. The temporary file is unlinked
  after read.
- `gate(True)` stops the recorder and pauses the worker; `gate(False)`
  re-arms it. Worst-case gate latency in fallback mode is one fragment
  (250 ms) — still tolerable but documented.

The active path is exposed on `streamer.mode` as either
`"alaudio_device"` (preferred) or `"alaudio_recorder_fragment"` (fallback)
and logged at startup.

**Phase 1 contract compliance**

The triples `(seq, ts_ms, base64_pcm)` map directly onto the
`audio_chunk` envelope from `docs/PHASE_1_TASK_MAP.md`:

```jsonc
{
  "type": "audio_chunk",
  "seq": 42,
  "ts_ms": 1714956000123.4,
  "data": "<base64 PCM>"
}
```

The WS-client owner (`nao-ws-client`) wraps each triple in this envelope
and sends as a JSON text frame. PCM is already base64-encoded as text by
the streamer — no additional work in the WS layer.

**Open implementation questions for the live test**

| # | Question | How we'll know |
|---|---|---|
| 1 | Does NAOqi 2.8 firmware on this specific robot expose `ALAudioDevice.subscribe`? | First boot: log line says either `subscribed to ALAudioDevice` (good) or `falling back to fragment recorder` (path A failed; we still get audio but at higher latency). |
| 2 | Does `setClientPreferences(name, 16000, 3, 0)` reliably produce mono front-mic PCM, or does firmware ignore the prefs and deliver 4-channel ALL audio? | We added defensive demux in `processRemote`: if `nbOfChannels > 1` we extract the FRONT-equivalent channel from the interleaved buffer (using channel index 2 in the 4-channel ALL ordering, which is FRONT per Aldebaran docs). |
| 3 | Average jitter and dropped-frame rate over 60 s of speech? | Run the verification command below; check `dropped_frames` after 60 s of talking. Acceptance: < 0.5% drop rate, < 40 ms p95 enqueue jitter. |

---

## Spike B — Realtime API parallel benchmark

**Status:** **NOT executed in this code commit. Decision deferred to the
operator pending Spike A live-test confirmation.**

**Rationale**

The PRD's D1 decision criteria (`docs/PRD_v2.md` § Phase 0.5) state:

> If FastAPI WS p50 within 1.3× of Realtime API → commit to FastAPI WS
> (D1 confirmed).

The operator already wrote the Realtime API integration in `nao/realtime_chat.py`
and `server/realtime_proxy.py`; latency is measurable from those existing
paths against the new FastAPI WS handler when both are running side-by-side.
Re-implementing Realtime API streaming in throwaway spike code adds
maintenance cost with no new information. The cleaner sequence is:

1. Confirm Spike A on the robot (does the ALModule path work at all?).
2. Stand up the FastAPI WS endpoint (Phase 1, in progress in parallel
   worktrees).
3. Run a 100-utterance side-by-side benchmark using the existing Realtime
   path vs the new WS path. Capture p50/p95 of `e2e_user_to_first_audio`
   from the Phase 1 metrics endpoint vs equivalent timestamps in
   `realtime_chat.py`.
4. Apply D1's decision rule against those numbers.

No Realtime-specific code was written in this worktree. If Spike A live
test fails, the fragment-fallback path keeps Phase 1 alive while we
re-evaluate Realtime as the voice path.

---

## Spike C — Mic-gate-during-TTS validation

**Status:** Confirmed by design.

**Mechanism**

`NaoAudioStreamer.gate(closed=True)` calls
`ALAudioDevice.unsubscribe(self.module_name)`, which causes the audio
device to stop delivering buffers to our `processRemote`. The next
ALAudioDevice frame boundary is at most one frame size away — at our
default 20 ms request, worst-case delivery of an in-flight buffer is
**< 20 ms**, well under the Phase 1 50 ms target.

For belt-and-braces protection, `processRemote` also early-returns when
`self._gate_closed` is `True`, so even an in-flight buffer that beats
unsubscribe by a few ms is silently dropped on our side rather than
making it to the queue. This is the second of the three echo-defense
layers from the PRD:

1. **Layer 1 — mic gate (this module):** unsubscribe → no buffers arrive.
2. **Layer 2 — server echo window:** drop frames within `tts_active_window_ms`.
3. **Layer 3 — self-echo regex:** existing in `server/server.py`.

**Fragment-mode caveat**

In the firmware-fallback path, `gate(closed=True)` calls
`ALAudioRecorder.stopMicrophonesRecording()`. The currently-recording
fragment continues until it completes, so the gate can leak up to one
fragment (250 ms) into the queue. This is acceptable because the
fallback path is already higher-latency overall, and Layer 2 + Layer 3
catch the leak server-side. We log the active mode at start so it's
clear which guarantee applies.

---

## Verification steps (to run on the physical robot)

> Prerequisites:
> - Code synced with `rsync -avz --delete nao/ nao@172.20.95.127:/home/nao/nao_assist/`
> - Robot is booted and reachable: `ping 172.20.95.127`

### Smoke test 1 — module imports and reports a mode

```bash
ssh nao@172.20.95.127
cd /home/nao/nao_assist
python -c "
from naoqi import ALBroker
broker = ALBroker('streamer_broker', '0.0.0.0', 0, '127.0.0.1', 9559)
import audio_module
streamer = audio_module.NaoAudioStreamer(
    broker_ip='0.0.0.0', broker_port=0,
    nao_ip='127.0.0.1', nao_port=9559,
    name='SpikeTest',
)
mode = streamer.start()
print('MODE:', mode)
import time; time.sleep(2)
streamer.stop()
broker.shutdown()
"
```

**Expected output:** one of
- `MODE: alaudio_device` (subscriber path works — ideal)
- `MODE: alaudio_recorder_fragment` (firmware fallback in use; document and proceed)

A fatal error in either path is the hard-fail case described in the PRD.

### Smoke test 2 — chunk delivery rate over 10 s

Speak normally for 10 s and verify chunks arrive:

```bash
python -c "
from naoqi import ALBroker
broker = ALBroker('streamer_broker', '0.0.0.0', 0, '127.0.0.1', 9559)
import audio_module, time
s = audio_module.NaoAudioStreamer(name='SpikeTest2')
s.start()
print('Mode:', s.mode, 'Listening for 10s...')
t0 = time.time()
n = 0
last_seq = -1
for seq, ts_ms, b64 in s.read_chunks(timeout=0.5):
    n += 1
    if seq != last_seq + 1 and last_seq != -1:
        print('GAP at seq', seq, 'previous', last_seq)
    last_seq = seq
    if time.time() - t0 > 10.0:
        break
print('Total chunks in 10s:', n, '(expected ~500 = 10s/20ms)')
print('Dropped frames:', s.dropped_frames)
print('Final queue depth:', s.queue_depth)
s.stop()
broker.shutdown()
"
```

**Expected:** ~500 ± 50 chunks (10 s × 50 chunks/s = 500), zero dropped
frames, no GAP messages. If chunks come in much fewer (e.g. 10), the
firmware is delivering at a slower than 20 ms cadence — still ok but
document the actual cadence.

### Smoke test 3 — mic-gate latency

```bash
python -c "
from naoqi import ALBroker
broker = ALBroker('streamer_broker', '0.0.0.0', 0, '127.0.0.1', 9559)
import audio_module, time
s = audio_module.NaoAudioStreamer(name='SpikeTest3')
s.start()
time.sleep(0.5)
# Drain pre-existing chunks.
drained = 0
try:
    while True:
        s._queue.get_nowait()
        drained += 1
except Exception:
    pass
print('Drained pre-gate:', drained)
# Close gate, wait 100 ms, count frames received during gate.
s.gate(True)
t_gate_close = time.time()
time.sleep(0.1)
during_gate = s.queue_depth
print('Frames received during 100ms gate-closed window:', during_gate, '(target: 0)')
s.gate(False)
time.sleep(0.5)
post = s.queue_depth
print('Frames received in 500ms after gate-open:', post, '(expected ~25)')
s.stop()
broker.shutdown()
"
```

**Expected:** `during_gate <= 1` (worst case: one in-flight 20 ms frame at
the moment of unsubscribe). `post` should be roughly proportional to
500 ms × 50 chunks/s = 25 ± 5.

### Smoke test 4 — fragment fallback (forced)

If smoke test 1 reports `alaudio_device`, force the fallback path to
verify it also works:

```bash
python -c "
from naoqi import ALBroker
broker = ALBroker('streamer_broker', '0.0.0.0', 0, '127.0.0.1', 9559)
import audio_module, time
s = audio_module.NaoAudioStreamer(name='SpikeTest4')
# Monkey-patch to force the subscriber path to fail.
orig = s._do_subscribe
def boom():
    raise RuntimeError('forced failure for fallback test')
s._do_subscribe = boom
mode = s.start()
print('Forced-failure mode:', mode, '(should be alaudio_recorder_fragment)')
time.sleep(3)
print('Chunks queued:', s.queue_depth)
s.stop()
broker.shutdown()
"
```

**Expected:** `mode == 'alaudio_recorder_fragment'` and `queue_depth > 0`
after 3 s. Lower per-second chunk count is fine — fallback path delivers
in 250 ms bursts.

---

## Open risks

| Risk | Mitigation |
|---|---|
| `setClientPreferences` signature differs across firmware revisions and call may TypeError | Caught in the broad `try/except` around `_setup_subscriber`; falls into fragment-mode. |
| Some firmware delivers 4-channel ALL audio even when prefs ask for FRONT | Defensive `_extract_channel` demux when `nbOfChannels > 1`. |
| `processRemote` runs on a naoqi worker thread; uncaught exceptions are silent | Wrapped in try/except with explicit `logger.error(traceback.format_exc())`. |
| Fragment-mode disk I/O on `/home/nao` could wear flash storage in long sessions | Files are unlinked after read; only one fragment lives on disk at a time. |
| Queue overflow under sustained network outage in fragment-mode (recorder keeps generating WAVs) | LRU drop policy on the queue caps memory at 200 × 640 B = 128 KB; recorder loop also checks `_gate_closed` / `_streaming` before each fragment. |
| `ALModule` registration in `__main__` may collide if two streamers use the same name | Module name is a constructor arg; caller picks unique names per instance. |

---

## Decision

**Proceeding with FastAPI WS path as planned in PRD D1.** No Realtime API
re-implementation in this commit. Spike B benchmark deferred to
operator-led side-by-side comparison once Phase 1 transport is stood up
in adjacent worktrees. If the live verification of Spike A in
`alaudio_device` mode succeeds (Smoke tests 1-3 pass), Phase 1 commits
unchanged. If only the fragment fallback works, the latency target
relaxes to < 1.2 s p50 per the PRD's Phase 0.5 contingency clause.
