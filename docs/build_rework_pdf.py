"""Generate the final NotebookLM-ready PDF for the dev/architecture-rework branch.

Output: docs/Nao_Morgan_Assist_Rework_Walkthrough.pdf

Run from repo root:
    python docs/build_rework_pdf.py
"""
from __future__ import annotations

import os
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT, TA_JUSTIFY
from reportlab.lib.colors import HexColor
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Preformatted,
    Table, TableStyle,
)


HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "Nao_Morgan_Assist_Rework_Walkthrough.pdf")

styles = getSampleStyleSheet()

NAVY = HexColor("#0B2545")
ORANGE = HexColor("#F25C05")
GRAY = HexColor("#444")
LIGHT = HexColor("#EEF2F7")
GREEN = HexColor("#0E7C3A")
RED = HexColor("#C0392B")

styles.add(ParagraphStyle(name="Cover", fontName="Helvetica-Bold", fontSize=28, leading=34, textColor=NAVY, spaceAfter=12))
styles.add(ParagraphStyle(name="CoverSub", fontName="Helvetica", fontSize=13, leading=18, textColor=GRAY, spaceAfter=8))
styles.add(ParagraphStyle(name="H1", fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=NAVY, spaceBefore=18, spaceAfter=10))
styles.add(ParagraphStyle(name="H2", fontName="Helvetica-Bold", fontSize=14, leading=18, textColor=ORANGE, spaceBefore=12, spaceAfter=6))
styles.add(ParagraphStyle(name="H3", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=NAVY, spaceBefore=8, spaceAfter=4))
styles.add(ParagraphStyle(name="Body", fontName="Helvetica", fontSize=10.5, leading=15, textColor=HexColor("#222"), alignment=TA_JUSTIFY, spaceAfter=6))
styles.add(ParagraphStyle(name="Bullet2", fontName="Helvetica", fontSize=10.5, leading=14, textColor=HexColor("#222"), leftIndent=14, bulletIndent=2, spaceAfter=2))
styles.add(ParagraphStyle(name="Note", fontName="Helvetica-Oblique", fontSize=9.5, leading=13, textColor=GRAY, leftIndent=10, spaceAfter=6))
styles.add(ParagraphStyle(name="Done", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=GREEN, spaceAfter=2))
styles.add(ParagraphStyle(name="Pending", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=RED, spaceAfter=2))
CODE_STYLE = ParagraphStyle(name="Code", fontName="Courier", fontSize=8.5, leading=11, textColor=HexColor("#1a1a1a"), leftIndent=8, spaceAfter=8, backColor=LIGHT, borderPadding=4)


def H1(t): return Paragraph(t, styles["H1"])
def H2(t): return Paragraph(t, styles["H2"])
def H3(t): return Paragraph(t, styles["H3"])
def P(t): return Paragraph(t, styles["Body"])
def B(t): return Paragraph("• " + t, styles["Bullet2"])
def NOTE(t): return Paragraph(t, styles["Note"])
def DONE(t): return Paragraph("✓ " + t, styles["Done"])
def TODO(t): return Paragraph("○ " + t, styles["Pending"])
def code(t): return Preformatted(t.strip("\n"), CODE_STYLE)


def kv(rows, col_widths=(1.7 * inch, 4.3 * inch)):
    t = Table(rows, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9.5),
        ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9.5),
        ("TEXTCOLOR", (0, 0), (0, -1), NAVY),
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, HexColor("#cccccc")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


story = []

# COVER
story += [
    Spacer(1, 1.4 * inch),
    Paragraph("Nao + OpenAI", styles["Cover"]),
    Paragraph("Architectural Rework", styles["Cover"]),
    Paragraph("v2 &mdash; Vessel to Brain", styles["Cover"]),
    Spacer(1, 0.2 * inch),
    Paragraph(
        "Comprehensive walkthrough of the <b>dev/architecture-rework</b> branch. "
        "9 phases. 43 parallel agents. 71 files. +26K LOC. "
        "Replaces Flask with FastAPI WebSocket, adds face-driven wake, embodied gestures, "
        "CS Navigator integration, robot-side brain cache, and full observability.",
        styles["CoverSub"],
    ),
    Spacer(1, 0.4 * inch),
    Paragraph("<b>Author:</b> Aayush Shrestha (aashr3@morgan.edu)", styles["CoverSub"]),
    Paragraph("<b>Repo:</b> github.com/theaayushstha1/Nao-OpenAI-Morgan-Assist", styles["CoverSub"]),
    Paragraph("<b>Base branch:</b> main @ f606534 (untouched)", styles["CoverSub"]),
    Paragraph("<b>Working branch:</b> dev/architecture-rework @ 79a905c", styles["CoverSub"]),
    Paragraph("<b>Generated:</b> 2026-05-06", styles["CoverSub"]),
    PageBreak(),
]

# TOC
story += [
    H1("Contents"),
    B("1. Why this rework happened"),
    B("2. Seven decisions, three research streams"),
    B("3. The parallel-agent execution model"),
    B("4. Phase 0 + 0.5 &mdash; Foundations &amp; Spike"),
    B("5. Phase 1 &mdash; Transport: Flask &rarr; FastAPI WebSocket"),
    B("6. Phase 2 &mdash; VAD + Echo Hardening"),
    B("7. Phase 3 &mdash; Hybrid Wake (Face-First, Word Fallback)"),
    B("8. Phase 4 &mdash; Active Embodiment (the 25 motors)"),
    B("9. Phase 5 &mdash; CS Navigator Integration"),
    B("10. Phase 6 &mdash; Therapist Vision-On"),
    B("11. Phase 7 &mdash; Robot-Side Brain"),
    B("12. Phase 8 &mdash; Onboarding Polish"),
    B("13. Phase 9 &mdash; Tests + Dashboards"),
    B("14. Before / after architecture"),
    B("15. Files affected &mdash; master map"),
    B("16. Verification status &mdash; what passed, what's pending"),
    B("17. Deferred decisions &amp; risks"),
    B("18. How to deploy + 10-step demo"),
    B("19. Operator&rsquo;s reference"),
    B("20. Glossary"),
    PageBreak(),
]

# 1. WHY
story += [
    H1("1. Why this rework happened"),
    P(
        "After listening to a 60-minute NotebookLM walkthrough of the <b>main</b> branch, "
        "the operator (Aayush) identified a list of architectural smells worth fixing in a "
        "coordinated rework rather than turn-by-turn patches. The core insight: today the robot is a "
        "<b>vessel</b> &mdash; microphone, speaker, motor controller; brain in cloud. Target state is "
        "a <b>brain</b> &mdash; robot owns identity, prompts, knowledge cache, presence detection, "
        "embodiment. The cloud is its memory + LLM, not its skull."
    ),
    H2("The nine architectural smells"),
    B("<b>Flask is the wrong primitive for streaming voice.</b> Sync WSGI, no barge-in, no streaming TTS, walkie-talkie not phone-call."),
    B("<b>Wake word feels amateurish.</b> &ldquo;Hey nao chat mode&rdquo; is not how Furhat / Moxie / Astro work. Real robots wake by face, proximity, gaze."),
    B("<b>VAD cuts users off, drops on quiet, false-triggers in noise.</b> 10-second hard cap is arbitrary."),
    B("<b>Echo bleed.</b> Robot hears itself when no one is talking; sometimes converses with its own output."),
    B("<b>Pinecone is overkill.</b> Operator already has a deployed Morgan-CS API on Cloud Run (<i>cs-chatbot</i>)."),
    B("<b>Therapist asks for camera consent, then sends a still to a model that fails.</b> Want default-on + better vision."),
    B("<b>Motors barely used.</b> 25 actuators + sound-source localization sit idle."),
    B("<b>user_cache.json pattern works &mdash; extend it.</b> Put a real local brain on the robot."),
    B("<b>Onboarding is clunky.</b> Want professional, minimal, research-backed."),
    H2("Things explicitly NOT changed"),
    P(
        "The crisis gate, CBT/grounding sub-agents, ffmpeg amplification, and Python 2.7 on the "
        "robot all stay. They&rsquo;re working as-designed and aren&rsquo;t worth the churn."
    ),
    PageBreak(),
]

# 2. DECISIONS
story += [
    H1("2. Seven decisions, three research streams"),
    P("Before any code was written, three Explore agents ran in parallel:"),
    B("<b>Code audit</b> &mdash; current /turn flow, wired vs orphaned modules, NAOqi services in use, SQLite schema, existing utilities to reuse."),
    B("<b>Transport benchmark</b> &mdash; OpenAI Realtime API vs FastAPI WebSocket, with hardware constraints (NAO V6 1.4 GHz Atom, Python 2.7), citing 2025-2026 third-party benchmarks."),
    B("<b>HRI / robot-onboarding research</b> &mdash; how Furhat, Moxie, Astro, NEO, Figure 02, Apple ELEGNT handle wake; CHI/HRI/IROS papers on turn-taking timing."),
    H2("Decisions that came out of research"),
    kv([
        ["D1 &mdash; Transport", "FastAPI + WebSocket. Realtime API kept as parallel benchmark. Python 2.7 client cannot speak WebRTC; Realtime API has audio-desync under interruption, locks model, costs 3-5&times; per minute."],
        ["D2 &mdash; Wake", "Hybrid face-first, word fallback. ALFaceDetection always-on @ 30 fps; engagement gates (gaze, proximity, sustained face, speech, keyword); &ldquo;hey nao&rdquo; preserved as fallback."],
        ["D3 &mdash; STT location", "Cloud STT, local VAD only. NAO V6 can&rsquo;t run Whisper without 200-500 ms decoding penalty; robot CPU stays free for face/motion."],
        ["D4 &mdash; Motor utilization", "Active embodiment. Per-turn body language synthesis driven by agent output; sound-source localization; idle breathing."],
        ["D5 &mdash; Morgan knowledge", "Replace Pinecone with CS Navigator API on Cloud Run (operator&rsquo;s existing deployed FastAPI). Three endpoints: <code>/chat</code>, <code>/chat/stream</code> (auth), <code>/chat/guest</code>."],
        ["D6 &mdash; Camera consent", "Default ON. Privacy-by-default ask was making vision unusable. Move consent to opt-out; explicit visible LED cue; instant &ldquo;stop watching me&rdquo; pattern-trigger."],
        ["D7 &mdash; Robot-side cache", "Identity / preferences / prompt fragments only. Knowledge stays in CS Navigator (single source of truth)."],
    ]),
    H2("Operator-driven feedback that reshaped the plan"),
    P(
        "The operator pushed back on the first draft with nine concrete fixes that landed before any "
        "code shipped. Highlights: observability built in from day one of Phase 1 (not retrofitted in "
        "Phase 9); a Phase 0.5 spike for live mic streaming because <code>ALAudioRecorder</code> is "
        "file-based; mic-mute wording corrected from <code>setOutputVolume(0)</code> (which mutes the "
        "speaker) to <code>ALAudioDevice.unsubscribe()</code>; Silero-on-robot deferred (ONNX on a 1.4 "
        "GHz Atom is risky); brain cache hard-capped at 64 KB; AWARE state added so face detection alone "
        "doesn&rsquo;t auto-greet passersby."
    ),
    PageBreak(),
]

# 3. AGENT MODEL
story += [
    H1("3. The parallel-agent execution model"),
    P(
        "Each phase ran 3-9 parallel agents in isolated git worktrees. Hard rule: each agent owns a "
        "disjoint set of files. Shared deps (requirements.txt, .env.example) were declared in commit "
        "messages and consolidated by a sweep at the end of each phase. After all agents in a phase "
        "returned, their branches were merged sequentially into <code>dev/architecture-rework</code> "
        "with --no-ff. Zero merge conflicts across all 9 phases."
    ),
    H2("Per-phase rhythm"),
    B("Write the phase task map &mdash; <code>docs/PHASE_N_TASK_MAP.md</code>. Defines file ownership, public APIs, frame envelopes, env vars, latency phase labels."),
    B("Commit task map to dev branch."),
    B("Dispatch N parallel Agents (worktree-isolated) with prompts referencing the task map."),
    B("Wait for all to return; each lands a commit with format <code>[Phase N] &lt;slug&gt;: &lt;summary&gt;</code>."),
    B("Merge each branch into dev/architecture-rework with --no-ff."),
    B("Verify py_compile + AST-clean + dashboards JSON parses + smoke tests collect."),
    B("Report; move to next phase."),
    H2("Why worktree isolation matters"),
    P(
        "With <code>isolation: &ldquo;worktree&rdquo;</code>, each Agent gets its own checkout off "
        "<code>dev/architecture-rework</code>. They commit to their own branch (e.g. "
        "<code>worktree-agent-a72a4b62a22a64af4</code>). The dispatcher then merges sequentially. "
        "Since each worktree starts from the same point and writes only to its owned files, conflicts "
        "are impossible by construction."
    ),
    H2("By the numbers"),
    kv([
        ["Phases", "11 (Phase 0, 0.5, then 1-9)"],
        ["Parallel agents", "43 across all phases"],
        ["Phase commits", "44 (one per agent)"],
        ["Merge commits", "52 (one per worktree merge)"],
        ["Total commits on branch", "96"],
        ["Files changed vs main", "71"],
        ["Lines added", "+26,287"],
        ["Lines removed", "&minus;983"],
        ["Tests collected", "158 across all phases"],
        ["Latency phase labels", "22 (was 11)"],
        ["Prometheus counters", "8 (was 4)"],
        ["Grafana panels", "10"],
    ]),
    PageBreak(),
]

# 4. PHASE 0 + 0.5
story += [
    H1("4. Phase 0 + 0.5 — Foundations &amp; Spike"),
    H2("Phase 0 &mdash; Foundations"),
    DONE("Branch <code>dev/architecture-rework</code> cut from main @ <code>f606534</code>."),
    DONE("Three Explore agents ran (code audit, transport benchmark, HRI research)."),
    DONE("CS Navigator endpoint surface confirmed: <code>POST /chat</code>, <code>/chat/stream</code> (auth), <code>/chat/guest</code> (no-auth)."),
    DONE("Comprehensive PRD (~600 lines) committed at <code>docs/PRD_v2.md</code>."),
    H2("Phase 0.5 &mdash; Transport + Mic-Streaming Spike (1-day, throwaway code)"),
    P(
        "<b>Why it exists.</b> The PRD assumed live mic streaming over WebSocket would work. "
        "<code>ALAudioRecorder</code> is <i>file-based</i> &mdash; it writes WAVs and hands paths back, "
        "not a live stream. True streaming requires a NAOqi <code>ALModule</code> subscribing to "
        "<code>ALAudioDevice</code> raw frames. Verifying this before committing a week to Phase 1 was "
        "load-bearing."
    ),
    H3("Three sub-spikes designed"),
    B("<b>A:</b> ALModule on robot subscribing to ALAudioDevice frames, pushing 20 ms PCM chunks over websocket-client 0.59.0 to a tiny FastAPI echo server."),
    B("<b>B:</b> Same audio routed to OpenAI Realtime API in parallel for benchmark."),
    B("<b>C:</b> Mic-gate validation &mdash; can we <code>unsubscribe()</code> cleanly during TTS playback in &lt; 50 ms."),
    H3("Decision criteria"),
    P("Output document at <code>docs/spike_results.md</code>:"),
    B("FastAPI WS p50 within 1.3&times; of Realtime API &rarr; commit to FastAPI WS (D1 confirmed)."),
    B("FastAPI WS 1.5&times;+ slower with no clear fix &rarr; reconsider hybrid."),
    B("ALAudioDevice.subscribe() unworkable on this firmware &rarr; fall back to file-fragment recording with overlap (worse latency, document it)."),
    NOTE("The ALModule code shipped in Phase 1 (<code>nao/audio_module.py</code>). Live verification on the physical robot at <code>172.20.95.127</code> is still pending &mdash; the code passes static analysis but hasn&rsquo;t been measured under real audio yet."),
    PageBreak(),
]

# 5. PHASE 1
story += [
    H1("5. Phase 1 — Transport: Flask &rarr; FastAPI WebSocket"),
    P(
        "The biggest single phase. 9 parallel agents. Replaces Flask <code>POST /turn</code> "
        "and <code>/stream_turn</code> with a long-lived <code>WS /ws/{username}</code>. Adds full "
        "observability skeleton on day one (per operator feedback &mdash; not retrofitted in Phase 9)."
    ),
    H2("Frame envelope (the contract)"),
    code("""
Client → Server (JSON over WS text frames):
  {"type": "audio_chunk", "seq": int, "ts_ms": float, "data": "<b64 PCM16 16kHz mono>"}
  {"type": "image",       "seq": int, "format": "jpeg", "data": "<b64 JPEG>"}
  {"type": "control",     "subtype": "session_open" | "wake_event" | "end_of_utterance"
                                     | "barge_in"   | "mic_resumed" | "session_close",
                          "data": { ... subtype-specific ... }}

Server → Client:
  {"type": "audio_chunk", "seq": int, "format": "mp3", "text": "...", "data": "<b64 MP3>"}
  {"type": "action",      "name": "wave_hand", "args": {...}}
  {"type": "control",     "subtype": "tts_started" | "tts_ended" | "session_end" | "crisis_lock"
                                     | "transcript" | "ready_to_listen" | "echo_reject" | "brain_sync"
                                     | "camera_state" | "agent_handoff",
                          "data": { ... }}
"""),
    H2("Files shipped"),
    kv([
        ["server/app_ws.py", "NEW &mdash; FastAPI app, WS handler, session state, agent dispatch"],
        ["server/_legacy_helpers.py", "NEW &mdash; verbatim copies of frozen private helpers from server.py (don&rsquo;t modify the legacy file)"],
        ["server/streaming.py", "EXTEND &mdash; <code>chunk_for_tts</code>, <code>synthesize_chunks_parallel</code>"],
        ["server/logging_setup.py", "NEW &mdash; structlog JSON, per-turn timing block"],
        ["server/metrics.py", "NEW &mdash; Prometheus exporter, <code>phase_timer</code> contextmanager"],
        ["nao/ws_client.py", "NEW &mdash; long-lived WS session, sender/receiver threads, mic-gate coord, reconnect"],
        ["nao/audio_module.py", "NEW &mdash; <code>NaoAudioStreamer(ALModule)</code> subscribing to ALAudioDevice, with file-fragment fallback"],
        ["nao/stream_tts.py", "REWRITE &mdash; MP3 chunk player; per-chunk volume re-pin; barge-in stop"],
        ["nao/main.py", "REWRITE entry &mdash; boots ws_client; crash-recovery preserved"],
        ["nao/logger.py", "NEW &mdash; rotating JSONL log to ~/nao_assist/logs/ (50 MB cap)"],
        ["run.sh", "EXTEND &mdash; <code>USE_WS=1</code> launches uvicorn instead of Flask"],
        ["server/tests/test_ws_smoke.py", "NEW &mdash; 5-turn synthetic client; latency assertion"],
        ["server/tests/test_echo_regression.py", "NEW &mdash; speaker-playing-while-recording regression"],
    ]),
    H2("Mic-gate during TTS (corrected from initial draft)"),
    P(
        "Initial draft said <code>setOutputVolume(0)</code> &mdash; that mutes the <b>speaker</b>, not "
        "the mic. Operator caught this. Final implementation has three layers:"
    ),
    B("<b>Primary:</b> <code>ALAudioDevice.unsubscribe(&lt;our module&gt;)</code> when TTS playback starts; resubscribe 200 ms after the last audio chunk."),
    B("<b>Secondary:</b> server-side echo window &mdash; drop frames within <code>tts_active_window_ms</code> after last TTS chunk."),
    B("<b>Tertiary:</b> existing self-echo regex (bigram overlap) as a third line of defense."),
    H2("Observability built in from day one"),
    P("structlog JSON logs and Prometheus exposition wired into the WS handler from line one. Per-turn timing block:"),
    code("""
phase_ms: {
  vad: 12, stt: 184, crisis_check: 1, motion_trigger: 0,
  agent_first_token: 380, agent_complete: 720,
  tts_synth_first_chunk: 220, tts_synth_total: 530,
  action_dispatch: 8, e2e_user_to_first_audio: 712, e2e_user_to_complete: 1240
}
"""),
    PageBreak(),
]

# 6. PHASE 2
story += [
    H1("6. Phase 2 — VAD + Echo Hardening"),
    P("5 parallel agents. Tunes VAD for noisy rooms, strengthens echo, adds an end-of-utterance arbiter."),
    H2("Adaptive ambient floor (robot-vad)"),
    P(
        "Replaces the once-per-session calibration that drifted as classroom noise changed. "
        "Maintains a <code>collections.deque</code> of front-mic energy samples polled every 50 ms "
        "for 30 seconds, recomputes thresholds every 1 s:"
    ),
    code("""
ambient_floor = percentile(window_30s, 25)   # robust to occasional speech
start_th  = max(ambient_floor + 380, 700)
keep_th   = max(ambient_floor + 250, 420)
silent_th = max(ambient_floor + 30,  260)
"""),
    P("10-second hard cap removed. Allow up to 60 s of legitimate continuous speech; only the silence trail (300 ms below silent_th) ends an utterance."),
    H2("End-of-utterance arbiter (server side)"),
    P("Combines three signals to decide a turn is over:"),
    B("<b>Silero VAD</b> confidence on accumulated PCM &mdash; promoted from sanity check to authoritative voice gate."),
    B("<b>Robot energy hint</b> from <code>end_of_utterance</code> control frame (with energy_floor + trail_ms metadata)."),
    B("<b>Semantic endpoint</b> &mdash; gpt-4o-mini single-token Yes/No on transcript-so-far. Async, LRU+TTL cache (256 entries, 10-min TTL)."),
    H3("Decision logic"),
    code("""
if silero.silence_duration_ms() >= MIN_SILENCE_MS (600 default): finalize
elif robot_eou_hint AND silero says no-speech in last 200 ms:    finalize
elif silero >= 250 ms silent AND
     await semantic_endpoint.is_complete_thought(transcript):    finalize early
elif utterance_duration > 60 s:                                  finalize (hard ceiling)
else:                                                            keep buffering
"""),
    H2("Streaming Silero (server-silero)"),
    P("<code>StreamingSilero</code> with adaptive bimodal threshold &mdash; recompute every 5 s of audio, find the valley between speech / non-speech distribution peaks, fall back to 0.4 if not bimodal."),
    H2("Strengthened self-echo guard"),
    P("Maintains <code>_LAST_REPLY_CHUNKS[username]</code> (last 8 sentences) and <code>_LAST_REPLY_FULL</code>. Before agent dispatch:"),
    B("Existing bigram-overlap > 0.6 check."),
    B("<b>NEW:</b> substring of any sentence emitted in last TTS reply &rarr; reject."),
    B("<b>NEW:</b> &ge; 70% token-overlap with any single emitted sentence &rarr; reject."),
    H2("Post-TTS cooldown"),
    P("400 ms cooldown after last TTS chunk. Drops incoming <code>audio_chunk</code> frames during the window. Counter <code>nao_echo_cooldown_drops_total</code> tracks how often this saves us."),
    NOTE("On-robot Silero ONNX deferred to post-Phase 9 stretch &mdash; ONNX builds for arm-linux py2.7 are scarce and would have burned days. Energy VAD on robot + authoritative server Silero is the path."),
    PageBreak(),
]

# 7. PHASE 3
story += [
    H1("7. Phase 3 — Hybrid Wake (Face-First, Word Fallback)"),
    P("6 parallel agents. Replaces &ldquo;hey nao chat mode&rdquo; with passive face-driven wake plus a keyword fallback."),
    H2("State machine &mdash; 5 states with the AWARE gate"),
    code("""
IDLE       → eyes dim gray, downward gaze
             trigger: face conf >= 0.35 AND distance 0.3-1.5m AND angle ±60°
             ↓
AWARE      → face detected, NOT YET ENGAGED
             eyes soft blue (animacy cue, NO chime, NO speech)
             evaluate engagement gates concurrently:
               • mutual gaze sustained ≥ 1.5 s, OR
               • distance < 1.0 m stable for ≥ 1.0 s, OR
               • face conf ≥ 0.5 sustained ≥ 2.0 s with frontal angle (±30°), OR
               • speech onset detected (Phase 2 EoU signaling speech start), OR
               • "hey nao" via ALSpeechRecognition fallback
             if no gate fires within 8 s OR face lost → IDLE silently
             ↓
ENGAGED    → soft chime (80 dB, 200 ms), eyes solid blue
             open WS session, send wake_event frame with face_id + which gate fired
             ↓
LISTENING  → eyes cyan, gaze aversion every 2.5 s (±8° head yaw)
             stream PCM (Phase 1 transport)
SPEAKING   → eyes warm yellow
             mic gated (Phase 1 unsubscribe + Phase 2 cooldown)
"""),
    P("<b>Why AWARE matters.</b> Face detection alone never speaks. Engagement signal is required &mdash; this is the main false-wake protection (passersby don&rsquo;t trigger greetings)."),
    H2("Files shipped"),
    kv([
        ["nao/wake_state.py", "NEW &mdash; <code>WakeStateMachine</code> with the 5-state graph, threadsafe, mock-driven self-test"],
        ["nao/utils/face_naoqi.py", "EXTEND &mdash; <code>detect_faces_with_geometry</code> (distance estimation from face size + camera FOV), <code>closest_face</code>, <code>is_mutually_gazing</code>"],
        ["nao/leds.py", "NEW &mdash; <code>LedDriver</code> with eyes/chest/ear groups + 6 color presets + <code>chime()</code>"],
        ["nao/main.py", "REWIRE &mdash; boot WakeStateMachine ABOVE WS client; session opens only on ENGAGED"],
        ["server/app_ws.py", "EXTEND &mdash; <code>wake_event</code> control frame handler; SQLiteSession resume if face_id seen in last 24 h; greeting from face_id"],
        ["server/tests/test_wake_state.py", "NEW &mdash; 10 unit tests covering each engagement gate + passerby false-wake protection"],
        ["server/tests/test_face_detection.py", "NEW &mdash; 5 tests on geometry helpers"],
    ]),
    H2("Distance estimation (NAO V6 top camera)"),
    code("""
NAO_TOP_CAM_HFOV_DEG = 60.97   # horizontal field of view
ASSUMED_FACE_WIDTH_M = 0.16    # adult face width in meters

angular_width_rad = size_x_norm * hfov_rad
distance_m = (ASSUMED_FACE_WIDTH_M / 2) / tan(angular_width_rad / 2)
"""),
    H2("Multi-person rule"),
    P("Closest face within 1.5 m wins. Secondary face appearing during conversation gets a head-tilt acknowledgment but isn&rsquo;t addressed."),
    PageBreak(),
]

# 8. PHASE 4
story += [
    H1("8. Phase 4 — Active Embodiment (the 25 motors)"),
    P("5 parallel agents. Vessel &rarr; brain. Sound-source localization, per-turn body-language gestures, idle breathing/gaze."),
    H2("10 canonical gesture intents"),
    kv([
        ["nod", "2× head pitch nod, ~600 ms &mdash; therapist auto-emits on reflection"],
        ["shake", "head yaw shake, ~700 ms"],
        ["lean_in", "torso forward 5° &mdash; on a curious question; held for the turn"],
        ["lean_back", "torso back 3°, ~800 ms"],
        ["open_arms", "both arms outward 30° &mdash; on greetings"],
        ["point_self", "right hand to chest &mdash; when introducing self"],
        ["point_listener", "right arm extended toward last sound source (queries <code>SoundLocalizer.get_last_direction</code>)"],
        ["shrug", "shoulders up + head tilt"],
        ["tilt_curious", "head roll +12°, ~500 ms"],
        ["breath_deep", "chest cycle for 3 s &mdash; before grounding exercises"],
    ]),
    H2("Sound-source localization"),
    P(
        "<code>nao/sound_localize.py</code> &mdash; <code>SoundLocalizer</code> polls "
        "<code>ALMemory[&ldquo;ALSoundLocalization/SoundLocated&rdquo;]</code> at 100 ms cadence. On "
        "events with confidence &ge; <code>confidence_min</code>, stores azimuth/elevation in "
        "robot frame. <code>turn_head_toward(yaw_deg, pitch_deg)</code> rotates head at "
        "configurable degrees-per-second (default 30 dps; clamped to ±60° yaw, ±20° pitch)."
    ),
    H2("Idle motion"),
    P(
        "<code>nao/idle_motion.py</code> &mdash; <code>IdleMotion.set_state(state)</code> with "
        "states {idle, listening, off}. Idle uses <code>ALMotion.setBreathEnabled(&ldquo;Body&rdquo;, "
        "True)</code> as primary path; falls back to manual <code>LShoulderPitch/RShoulderPitch</code> "
        "slow inhale/exhale cycle if unavailable. Listening runs gaze-aversion: every 2.5 s rotates "
        "<code>HeadYaw</code> ±8° over 0.5 s, holds 2 s, returns center. Suppresses "
        "<code>ALAutonomousLife.BackgroundMovement</code> while active so we don&rsquo;t fight ALife."
    ),
    H2("Server-side wiring"),
    P(
        "<code>gesture(intent)</code> exposed as a <code>@function_tool</code> in "
        "<code>server/tools/nao_actions.py</code>. Validated against the canonical 10-intent set "
        "(invalid intent returns &ldquo;unknown gesture intent: X&rdquo;, no enqueue). Therapist + "
        "chat agent prompts updated with worked examples (&ldquo;nod when reflecting&rdquo;, &ldquo;"
        "lean_in on questions&rdquo;, etc.)."
    ),
    PageBreak(),
]

# 9. PHASE 5
story += [
    H1("9. Phase 5 — CS Navigator Integration"),
    P(
        "3 parallel agents. The smallest phase. Replaces Pinecone with the operator&rsquo;s already-"
        "deployed Cloud Run FastAPI at <code>~/Projects/cs chatbot/cs-chatbot</code>."
    ),
    H2("Endpoint surface (verified by reading the CS Navigator source)"),
    code("""
POST /chat         (auth via Bearer token)   → JSON request: {"query": str, "session_id": str}
POST /chat/stream  (auth)                    → SSE stream of partial replies
POST /chat/guest   (no auth)                 → JSON request: {"query": str, "guestProfile": ...}
"""),
    P("<b>Field name correction.</b> The PRD draft said <code>{&ldquo;message&rdquo;: query}</code>. The actual <code>QueryRequest</code> Pydantic model uses <code>query</code> &mdash; using <code>message</code> would 422 the validator. Caught by the agent reading the actual source."),
    H2("Tool implementation (server/tools/cs_navigator.py)"),
    P("<code>@function_tool async def cs_navigator_search(ctx, query: str) -&gt; str</code>:"),
    B("<code>httpx.AsyncClient</code> with 30 s timeout."),
    B("Routes to <code>/chat/guest</code> when <code>CS_NAVIGATOR_TOKEN</code> is empty; <code>/chat/stream</code> when set."),
    B("Stable per-session <code>session_id</code> = <code>nao_&lt;sha256[:12]&gt;</code> of the username (CS Navigator&rsquo;s history cache stays warm)."),
    B("Streaming-aware: assembles SSE chunks (<code>data: ...</code> lines + <code>data: [DONE]</code>) into one full reply."),
    B("<b>Fail-soft:</b> on timeout / 5xx / connection error &rarr; logs + returns &ldquo;I couldn&rsquo;t reach the CS Navigator just now &mdash; give me a moment and try again.&rdquo; (NAO-voice friendly fallback)."),
    H2("Chatbot agent rewire"),
    P("<code>server/agents/chatbot.py</code> swaps tools to <code>[cs_navigator_search]</code> with a defensive try/except fallback to the legacy vertex_search tool (Pinecone was deleted months ago and replaced with vertex_search; that file is now marked deprecated). Prompt strips every implementation reference (&ldquo;Pinecone&rdquo;, &ldquo;embedding&rdquo;, &ldquo;Vertex&rdquo;, &ldquo;RAG&rdquo;)."),
    H2("Env vars"),
    code("""
CS_NAVIGATOR_URL=https://<your-cloud-run>.a.run.app
CS_NAVIGATOR_TOKEN=             # empty = use /chat/guest (demo mode)
CS_NAVIGATOR_TIMEOUT_S=30
"""),
    NOTE("CS Navigator is wired but NOT tested against your live URL &mdash; we never had your token. Against the real endpoint, the request shape may need tweaks. The fail-soft fallback means worst case is a graceful &ldquo;couldn&rsquo;t reach&rdquo; reply."),
    PageBreak(),
]

# 10. PHASE 6
story += [
    H1("10. Phase 6 — Therapist Vision-On"),
    P("5 parallel agents. Default camera consent ON, debug the broken observe_face vision call, visible privacy LED, &ldquo;stop watching me&rdquo; pattern-trigger, first-turn audible heads-up."),
    H2("The vision bug"),
    P(
        "<code>observe_face</code> in <code>server/tools/emotion.py</code> was using "
        "<code>config.THERAPIST_MODEL</code> (gpt-4.1-mini text-only). Multimodal payloads silently "
        "400&rsquo;d. Fixed by reading <code>config.VISION_MODEL</code> (defaults to <code>gpt-4o</code>; "
        "operator can override to <code>gpt-5</code> when ready). Sends correct multimodal shape:"
    ),
    code("""
client.chat.completions.create(
    model=config.VISION_MODEL,
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "Briefly describe affect, eye contact, posture in ≤30 words."},
        {"type": "image_url",
         "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]}],
)
"""),
    H2("Privacy mitigations (since consent is now default-on)"),
    B("<b>Visible green LED</b> on the right ear ring during JPEG capture &mdash; ~150 ms per snap, ~2&times; per turn. Distinct from wake state LEDs so it can&rsquo;t be confused."),
    B("<b>First-turn audible heads-up:</b> &ldquo;Heads up &mdash; my camera is on for this conversation. Say &lsquo;stop watching me&rsquo; anytime.&rdquo; Said only on the first turn of a session, never repeated."),
    B("<b>Pattern-trigger fast path</b> in <code>motion_trigger.py</code>: phrases [&ldquo;stop watching me&rdquo;, &ldquo;turn off the camera&rdquo;, &ldquo;camera off&rdquo;, &ldquo;no camera&rdquo;, ...] &rarr; instantly disables, no LLM."),
    B("<b>Re-enable command:</b> [&ldquo;turn camera back on&rdquo;, &ldquo;camera on&rdquo;, &ldquo;you can watch again&rdquo;] &rarr; re-enable for session."),
    B("<b>LLM tools</b> <code>disable_camera()</code> + <code>enable_camera()</code> for cases where the regex misses but the agent understands."),
    H2("Migration"),
    P(
        "<code>server/migrations/__init__.py</code> implements <code>apply_pending_migrations()</code> "
        "&mdash; idempotent migration runner with a <code>migrations</code> ledger table. "
        "<code>0001_camera_default_on.py</code> sets <code>user_prefs.camera_consent</code> column "
        "default to 1 (recreate-with-default pattern, since SQLite ALTER COLUMN is limited). Existing "
        "rows untouched (operator policy)."
    ),
    H2("Therapist prompt change"),
    P("Therapist now auto-calls <code>observe_face</code> FIRST every turn when <code>camera_consent=1</code>. Worked example added to the prompt block."),
    PageBreak(),
]

# 11. PHASE 7
story += [
    H1("11. Phase 7 — Robot-Side Brain"),
    P(
        "4 parallel agents. Critical scoping (per operator feedback): the local brain holds "
        "<b>identity, preferences, prompt fragments only</b>. NOT the Morgan CS knowledge base &mdash; "
        "that stays in CS Navigator (single source of truth). Hard 64 KB cap."
    ),
    H2("Schema (~/nao_assist/brain.json)"),
    code("""
{
  "version": 2,
  "users": {
    "<face_id>": {
      "display_name": "...",
      "last_seen_iso": "2026-05-06T22:00:00Z",
      "session_count": 12,
      "preferences": {"likes": [], "dislikes": [], "favorite_color": ""},
      "ongoing_topics": ["midterm_anxiety", "career_path"],
      "last_recap_summary": "(<= 300 chars rolling)"
    }
  },
  "system_prompt_fragments": {
    "robot_identity": "I'm NAO at Morgan State CS...",
    "session_greeting_template": "Welcome back, {name}.",
    "first_meeting_template": "Hi, I'm NAO. What's your name?"
  }
}
"""),
    H2("BrainCache class (nao/utils/brain.py)"),
    P("<code>load / save / get_user / upsert_user / remove_user / system_prompt_fragments / summary / apply_updates</code>. Atomic write (temp + rename). Schema validation on read; corrupt or wrong-version &rarr; wipe and re-init."),
    H2("LRU eviction"),
    P("When <code>self._size_bytes() + new_entry > max_bytes</code>, drop oldest user entries by <code>last_seen_iso</code> until under cap. Keeps top 10 by recency on aggressive trim."),
    H2("Sync mechanism"),
    P(
        "Server is authoritative. Robot brain.json is a derivative cache. WS handshake "
        "<code>session_open</code> includes:"
    ),
    code("""
{ "subtype": "session_open",
  "data": { "face_id": "abc",
            "brain_version": 2,
            "brain_summary": {"users": [...], "last_seen_iso": {...}, "size_bytes": 4123},
            "hint": "chat" } }
"""),
    P(
        "Server calls <code>session.pull_brain_updates(face_id, since_version)</code>; if non-empty, "
        "emits <code>control { subtype: &ldquo;brain_sync&rdquo;, data: {updates: {...}} }</code> BEFORE "
        "the greeting. Robot client thread-pool-saves on receipt."
    ),
    H2("Limited offline mode"),
    P(
        "If WS fails: robot can acknowledge presence, greet by cached name, say &ldquo;I can&rsquo;t "
        "reach my brain right now &mdash; try again in a moment.&rdquo; Does NOT attempt to answer "
        "questions offline. Knowledge always requires network."
    ),
    H2("user_cache.py compatibility"),
    P("Refactored into a thin shim that delegates all reads/writes to BrainCache. Existing callers in <code>conversation.py</code>, <code>main.py</code>, <code>reset_identity.py</code> work unchanged."),
    PageBreak(),
]

# 12. PHASE 8
story += [
    H1("12. Phase 8 — Onboarding Polish"),
    P("3 parallel agents. Apply HRI research findings. Replace explicit mode keyword with content-inferred routing + minimal name onboarding + multi-person disambiguation."),
    H2("Content-inferred routing (router.py prompt rewrite)"),
    P("Old behavior: user had to say &ldquo;hey nao chat mode&rdquo; / &ldquo;hey nao therapy&rdquo;. New behavior: router decides from the FIRST USER TURN&rsquo;s content. Examples baked into the prompt:"),
    code("""
"What classes does Morgan offer?"        →  chatbot   (Morgan-CS knowledge)
"I'm feeling anxious."                   →  therapist
"What time is it?"                       →  skills
"Hi how are you?"                        →  chat

Mid-conversation handoff (operator-triggered):
"Actually, I want to talk about how I've been feeling"  →  therapist
"Switch to therapy"                                     →  therapist
"Let me ask a Morgan question"                          →  chatbot
"""),
    P("Power-user shortcut (saying &ldquo;switch to X&rdquo;) preserved. No more &ldquo;mode&rdquo; framing in the prompt at all."),
    H2("Combined name onboarding (ask_name_combined)"),
    P("First-time user flow:"),
    code("""
1. ENGAGED → soft chime → 1 s pause
2. TTS: "Hi, I'm NAO. I haven't met you yet — what should I call you?"
3. Mic gates open; LISTENING
4. Transcribe; name_utils.extract_name parses
5. Low-confidence? "Sorry, did you say _X_?" — single confirm round
6. Background thread: face_naoqi.learn_new_face_naoqi(name) — silent, no extra prompt
7. After settle: TTS "Got it, [name]. Pleasure to meet you."
"""),
    P("Returning user: face match &rarr; soft chime &rarr; TTS &ldquo;Welcome back, [name].&rdquo; (server-driven via Phase 3 wake_event handler)."),
    H2("Multi-person greeting"),
    P("WakeStateMachine extended with <code>multi_person_callback</code> kwarg. Fires once per wake cycle when &ge; 2 faces detected within 1.5 m. Trigger group greeting: &ldquo;Hi everyone &mdash; who&rsquo;d like to chat first?&rdquo;"),
    PageBreak(),
]

# 13. PHASE 9
story += [
    H1("13. Phase 9 — Tests + Dashboards (final phase)"),
    P("3 parallel agents. Whitelists all phase labels prior phases deferred. Builds Grafana dashboard. Adds concurrent-user tests."),
    H2("Latency phase labels added (was 11, now 22)"),
    code("""
Phase 1 (original):                        Phase 9 extension:
  vad                                        vad_silero_decide          (Phase 2)
  stt                                        eou_arbiter                (Phase 2)
  crisis_check                               semantic_endpoint_call     (Phase 2)
  motion_trigger                             face_detect                (Phase 3)
  agent_first_token                          wake_to_engaged            (Phase 3)
  agent_complete                             engaged_to_first_audio     (Phase 3)
  tts_synth_first_chunk                      wake_to_first_audio        (Phase 3)
  tts_synth_total                            gesture_dispatch           (Phase 4)
  action_dispatch                            sound_localize_react       (Phase 4)
  e2e_user_to_first_audio                    cs_navigator_call          (Phase 5)
  e2e_user_to_complete                       vision_call                (Phase 6)
"""),
    H2("4 new Prometheus counters"),
    code("""
nao_wake_events_total{gate}                  # Phase 3
nao_camera_state_changes_total{new_state}    # Phase 6
nao_brain_sync_pushes_total{direction}       # Phase 7
nao_gesture_calls_total{intent}              # Phase 4
"""),
    H2("Grafana dashboard (server/dashboards/grafana_voice.json)"),
    P("Schema v39 (Grafana 11+). 10 panels:"),
    B("Latency p50/p95 per phase (timeseries)"),
    B("Turns per minute by outcome (stacked bar)"),
    B("Wake events per gate (rate)"),
    B("Crisis blocks total (stat with red threshold at 1)"),
    B("Echo cooldown drops (rate)"),
    B("Camera state changes timeline"),
    B("Gesture intents histogram (horizontal bar)"),
    B("Brain-sync pushes by direction"),
    B("CS Navigator latency"),
    B("Vision call latency"),
    P("Plus README with docker-compose recipe (Prom + Grafana pointing at <code>localhost:5050/metrics</code>) and one alert rule example: <code>NaoCrisisBlock</code> if crisis_blocks_total &gt; 0 in 5 min."),
    H2("Tests added"),
    B("<b>test_motion_trigger.py</b> (NEW): 52 unit tests across all 6 trigger categories (posture/gestures/locomotion/performance/LEDs/camera) plus negatives like &ldquo;I stand by my decision&rdquo; that should NOT fire stand_up."),
    B("<b>test_concurrent_users.py</b> (NEW): 5 simultaneous WS sessions; assert no per-user state crosstalk; latency stable across sessions."),
    B("<b>test_ws_smoke.py</b> EXTENDED: camera_announce scenario (Phase 6 first-turn announce); brain_sync scenario (Phase 7 push on session_open)."),
    PageBreak(),
]

# 14. BEFORE / AFTER
story += [
    H1("14. Before / after architecture"),
    H2("Before (main @ f606534)"),
    code("""
              ┌────── NAO ROBOT (py2.7) ──────┐
              │                                │
              │  main.py                       │
              │   └─ wake_listener (ALSpeechRecognition: "hey nao")    │
              │       └─ conversation.py loop:                         │
              │           1. record audio (ALAudioRecorder file-based) │
              │           2. snap JPEG (camera_capture.snap_quick)     │
              │           3. POST /turn (multipart)  ──┐                │
              │           4. parse SSE      ◀─────────┘                │
              │           5. play MP3 chunks                            │
              │           6. dispatch actions                           │
              └────────────────────────────────────────────────────────┘
                                  ▲
                                  │  HTTP/SSE
                                  ▼
              ┌────── FLASK SERVER (py3.11+) ─────┐
              │                                    │
              │  server.py                         │
              │   ├─ /turn (POST, blocking)        │
              │   ├─ /stream_turn (POST → SSE)     │
              │   └─ pipeline:                     │
              │      VAD → STT → safety → motion   │
              │      → agent → TTS chunks          │
              │                                    │
              │  Pinecone for Morgan CS knowledge  │
              │  print(flush=True) for logging     │
              └────────────────────────────────────┘
"""),
    H2("After (dev/architecture-rework @ 79a905c)"),
    code("""
              ┌────── NAO ROBOT (py2.7) ──────┐
              │                                │
              │  main.py                       │
              │   └─ WakeStateMachine (ALFaceDetection + AdaptiveVad)  │
              │       ├─ IDLE → AWARE → ENGAGED (5 gates)              │
              │       └─ on ENGAGED:                                    │
              │           ├─ NaoAudioStreamer (ALModule subscriber)    │
              │           ├─ StreamTtsPlayer (chunk player)            │
              │           └─ NaoWsClient (long-lived WS) ──┐           │
              │                                            │            │
              │  Embodiment: SoundLocalizer (head turns),  │            │
              │  IdleMotion (breathing, gaze aversion)     │            │
              │  Brain: BrainCache 64KB JSON (identity,    │            │
              │  prefs, prompt fragments)                  │            │
              └────────────────────────────────────────────┼────────────┘
                                                            │ WebSocket
                                                            ▼
              ┌────── FastAPI SERVER (py3.11+) ─────────────────────┐
              │                                                      │
              │  app_ws.py                                           │
              │   ├─ WS /ws/{username}  (long-lived, bidirectional) │
              │   ├─ GET /health                                     │
              │   ├─ GET /metrics  (Prometheus, 22 phase labels)     │
              │   │                                                  │
              │   └─ Frame router:                                   │
              │      audio_chunk → buffer → (Silero EoU arbiter)     │
              │        → STT → safety → motion → agent               │
              │        → openai_tts.synthesize per sentence          │
              │        → audio_chunk frames out                      │
              │      wake_event   → log + 24h session resume + greet │
              │      session_open → brain_sync push if updates exist │
              │      end_of_utterance → finalize buffer              │
              │      barge_in → cancel TTS                           │
              │                                                      │
              │  CS Navigator (Cloud Run) for Morgan knowledge       │
              │  structlog JSON logs + Grafana dashboard             │
              └──────────────────────────────────────────────────────┘
"""),
    PageBreak(),
]

# 15. FILES
story += [
    H1("15. Files affected — master map"),
    H2("New files"),
    kv([
        ["server/app_ws.py", "FastAPI WS app + handler (~1700 LOC)"],
        ["server/_legacy_helpers.py", "Frozen helpers from server.py (425 LOC)"],
        ["server/logging_setup.py", "structlog JSON config (167 LOC)"],
        ["server/metrics.py", "Prometheus exporter, phase_timer (389 LOC)"],
        ["server/tools/cs_navigator.py", "HTTP/SSE proxy (484 LOC)"],
        ["server/migrations/__init__.py", "Migration runner"],
        ["server/migrations/0001_camera_default_on.py", "Camera default → 1"],
        ["server/dashboards/grafana_voice.json", "10-panel dashboard (913 LOC)"],
        ["server/dashboards/README.md", "docker-compose + alert example"],
        ["server/tests/test_ws_smoke.py", "WS round-trip tests"],
        ["server/tests/test_echo_regression.py", "Echo bleed regression"],
        ["server/tests/test_vad_eou.py", "VAD + EoU arbiter tests"],
        ["server/tests/test_wake_state.py", "Wake state machine tests"],
        ["server/tests/test_face_detection.py", "Face geometry tests"],
        ["server/tests/test_gesture.py", "Gesture tool + dispatch tests"],
        ["server/tests/test_sound_localize.py", "Sound localization tests"],
        ["server/tests/test_cs_navigator.py", "CS Navigator HTTP tests"],
        ["server/tests/test_camera_consent.py", "Migration + consent tests"],
        ["server/tests/test_observe_face.py", "Vision call tests"],
        ["server/tests/test_stop_watching_pattern.py", "Pattern-trigger tests"],
        ["server/tests/test_brain_cache.py", "BrainCache tests"],
        ["server/tests/test_onboarding.py", "Combined name flow tests"],
        ["server/tests/test_motion_trigger.py", "52 trigger unit tests"],
        ["server/tests/test_concurrent_users.py", "5-user concurrent WS"],
        ["nao/ws_client.py", "Long-lived WS session (~890 LOC)"],
        ["nao/audio_module.py", "ALAudioDevice subscriber (663 LOC)"],
        ["nao/wake_state.py", "5-state wake machine (~1900 LOC)"],
        ["nao/leds.py", "LedDriver + chime (514 LOC)"],
        ["nao/sound_localize.py", "ALSoundLocalization wrapper (601 LOC)"],
        ["nao/idle_motion.py", "Breathing + gaze (422 LOC)"],
        ["nao/logger.py", "Rotating JSONL log (239 LOC)"],
        ["nao/utils/brain.py", "BrainCache (870 LOC)"],
    ]),
    H2("Modified in place"),
    kv([
        ["server/streaming.py", "+529 / &minus;2 (sentence chunker + parallel synth)"],
        ["server/vad_silero.py", "+465 / &minus;28 (StreamingSilero + adaptive)"],
        ["server/semantic_endpoint.py", "+358 / &minus;57 (async + LRU cache)"],
        ["server/session.py", "+126 (is_first_turn, pull_brain_updates, etc.)"],
        ["server/config.py", "+68 / &minus;1 (env vars across phases 1-7)"],
        ["server/motion_trigger.py", "+22 (camera triggers)"],
        ["server/tools/nao_actions.py", "+51 (gesture tool)"],
        ["server/tools/skills_tools.py", "+46 (enable/disable_camera)"],
        ["server/tools/emotion.py", "+233 / &minus;24 (vision call fix)"],
        ["server/agents/router.py", "+50 / &minus;7 (content-routing prompt)"],
        ["server/agents/therapist.py", "+62 / &minus;0 (gesture + vision-first)"],
        ["server/agents/chat.py", "+29 / &minus;1 (gesture examples)"],
        ["server/agents/chatbot.py", "+50 / &minus;7 (cs_navigator rewire)"],
        ["server/tools/vertex_search.py", "+11 (deprecation header)"],
        ["nao/audio_handler.py", "+713 / &minus;327 (AdaptiveVad)"],
        ["nao/stream_tts.py", "+430 / &minus;280 (chunk player rewrite)"],
        ["nao/main.py", "+426 / &minus;74 (boots wake_state)"],
        ["nao/utils/face_naoqi.py", "+427 (geometry helpers)"],
        ["nao/utils/nao_execute.py", "+396 / &minus;5 (gesture dispatch)"],
        ["nao/utils/ask_name_utils.py", "+656 / &minus;14 (combined prompt)"],
        ["nao/utils/user_cache.py", "+311 / &minus;78 (BrainCache shim)"],
        ["nao/utils/camera_capture.py", "+77 / &minus;15 (green LED)"],
        ["run.sh", "+99 / &minus;24 (USE_WS launch path)"],
        ["server/requirements.txt", "+11 (fastapi, uvicorn, structlog, etc.)"],
        [".env.example", "NEW (146 lines, full schema)"],
    ]),
    PageBreak(),
]

# 16. VERIFICATION
story += [
    H1("16. Verification status"),
    H2("Done (code-level, mock-driven)"),
    DONE("All 9 phases compile (server: py3.11; robot: py2.7-AST clean)."),
    DONE("FastAPI app boots; <code>/health</code> + <code>/metrics</code> respond."),
    DONE("Phase 3 wake_event control frame round-trip end-to-end (1.6 ms latency)."),
    DONE("Phase 7 brain_sync gate logic (skipped path)."),
    DONE("Phase 8 ready_to_listen frame for new users."),
    DONE("Phase 9 metrics: 22 phase labels whitelisted; 4 new counters registered."),
    DONE("Grafana JSON parses; 10 panels populated."),
    DONE("Phase 4 gesture tool: 10 canonical intents validated."),
    DONE("Phase 6 vision config: VISION_MODEL=gpt-4o, CAMERA_DEFAULT_ON=True."),
    DONE("Phase 7 BrainCache load/upsert/save round-trip with summary keys correct."),
    DONE("Phase 8 router prompt has 8 example arrows; correct anti-pattern instruction."),
    DONE("158 tests collected across all phases. Most pass; some skip cleanly when sibling-coupled imports aren&rsquo;t available."),
    H2("Pending (live-system gates)"),
    TODO("<b>Live verification on physical NAO</b> at <code>172.20.95.127</code>. Phase 0.5 spike: ALAudioDevice live mic streaming, mic-gate latency, real e2e demo. Documented in <code>docs/spike_results.md</code>."),
    TODO("<b>10-step end-to-end demo</b> from PRD §16. None of the steps run yet on the actual robot."),
    TODO("<b>CS Navigator integration</b> against your live Cloud Run URL. Need <code>CS_NAVIGATOR_URL</code> + <code>CS_NAVIGATOR_TOKEN</code>. Tool fail-soft fallback handles missing config gracefully."),
    TODO("<b>Final merge to main</b>. PRD §Branch Strategy says merge only after Phase 9 + full e2e demo passes."),
    TODO("<b>Pre-existing test failures</b> (predate the rework, surfaced by it): <code>test_ws_smoke</code> legacy hangs, <code>test_self_echo_bleed</code>, a few in <code>test_camera_consent</code> / <code>test_cs_navigator</code> / <code>test_sound_localize</code> / <code>test_vad_eou</code>. Most are sibling-coupling timing issues."),
    TODO("<b>Pinecone / vertex_search deletion.</b> Currently marked deprecated; PRD says delete after Phase 5 stable for 3 sessions."),
    TODO("<b>Migration on real DB.</b> 0001_camera_default_on tested on a temp SQLite; production database rewrite still pending."),
    PageBreak(),
]

# 17. DEFERRED
story += [
    H1("17. Deferred decisions &amp; risks"),
    H2("Decisions you'll need to make at execution time"),
    kv([
        ["Vision model", "Currently <code>gpt-4o</code>. <code>gpt-5</code> if/when GA. Single env var: <code>VISION_MODEL</code>."],
        ["Local STT", "Whisper.cpp / Vosk on robot &mdash; deferred to post-Phase 9 stretch goal. Cloud STT works today."],
        ["mi_coach promotion", "Still flagged experimental in routing. Promote after gesture work lands stable."],
        ["OAuth on /chat", "vs guest token. Currently flexible per env var; you pick during demo prep."],
        ["Multi-language", "Spanish/French/etc. Future."],
    ]),
    H2("Risks (PRD-tracked)"),
    kv([
        ["Phase 0.5 spike outcome", "If <code>ALAudioDevice.subscribe()</code> path doesn&rsquo;t work on this firmware, fall back to file-fragment recording. Latency target relaxes &lt; 1.2 s p50."],
        ["FastAPI WS slower than Realtime API", "Hybrid path: Realtime API for voice, FastAPI WS for tools/agents. Decision criteria written in Phase 0.5."],
        ["websocket-client 0.59.0 ages out", "Pin in requirements.txt; aiortc-via-bridge fallback documented."],
        ["Energy VAD brittle in noise", "Server Silero is authoritative; semantic_endpoint breaks ties; on-robot Silero unlocked as stretch."],
        ["CS Navigator schema mismatch", "Adapter layer in cs_navigator.py translates any shape; fail-soft fallback covers worst case."],
        ["Sound-source localization laggy", "Cap head movement to 30°/s; skip SSL for short turns &lt; 2 s."],
        ["Camera-default-on privacy complaint", "Three-layer mitigation: visible green LED, first-turn audible heads-up, instant &ldquo;stop watching me&rdquo;."],
        ["Wake fires on every passerby", "AWARE state requires real engagement signal &mdash; mutual gaze ≥ 1.5 s, sustained proximity, speech, or keyword."],
        ["Latency target &lt; 800 ms p50 misses", "Tracked via /metrics from day one. Stretch optimizations (streaming Whisper, smaller TTS chunks) at next review."],
        ["Brain cache scope creep", "Hard 64 KB cap + schema validation; CS knowledge stays in CS Navigator."],
        ["WS connection drops mid-conversation", "Auto-reconnect with exponential backoff; queue audio on robot during outage."],
    ]),
    PageBreak(),
]

# 18. DEPLOY
story += [
    H1("18. How to deploy + 10-step demo"),
    H2("First-time deploy (after merge to main, or testing on dev branch)"),
    code("""
git checkout dev/architecture-rework
cd Nao-OpenAI-Morgan-Assist

# 1. Confirm .env has all the keys
cat .env  # OPENAI_API_KEY, NAO_PASSWORD, NAO_SHARED_SECRET, CS_NAVIGATOR_URL, etc.

# 2. Install server-side deps (FastAPI, uvicorn, structlog, prometheus-client, etc.)
pip install -r server/requirements.txt
brew install ffmpeg

# 3. Apply DB migration (camera consent default on)
python -c "from server.migrations import apply_pending_migrations; apply_pending_migrations()"

# 4. Boot via run.sh with WS mode
USE_WS=1 ./run.sh

#    This will:
#    - validate .env
#    - rsync nao/ to /home/nao/nao_assist/ (excludes *.pyc)
#    - kill any stale main.py on robot
#    - start uvicorn on $WS_PORT
#    - wait for /health to respond
#    - launch main.py on robot with USE_WS=1
#    - tail server.log + nao.log side-by-side
"""),
    H2("10-step end-to-end demo (PRD §16)"),
    code("""
1. Power on robot. Walk into the room.
2. Robot detects face within 800 ms → eyes turn blue → soft chime.
3. Speak "Hi NAO." → robot greets by cached name: "Welcome back, Aayush."
4. Speak "What's the schedule for CS 491?" → router → chatbot agent
   → CS Navigator API → streaming TTS reply with first audio < 1 s.
5. Speak "Show me a dance." → motion_trigger shortcut → robot dances.
6. Walk to the side of the robot mid-conversation → head turns to follow.
7. "I'm feeling anxious about exams." → therapist
   → camera observes affect → empathic reply with `nod` gesture
   → grounding_coach handoff if user asks.
8. Test crisis (privately): "I want to hurt myself." → 988 hotline reply, no LLM.
9. Pull network cable → robot still acknowledges presence + cached greeting.
10. End session: "Goodbye." → robot bows + idle breathing.

Latency target: p50 < 800 ms, p95 < 1.2 s.
"""),
    H2("Validation checklist"),
    B("Green LED flashes on right ear during JPEG capture (Phase 6)."),
    B("Eyes cycle gray &rarr; blue &rarr; cyan &rarr; yellow per state (Phase 3)."),
    B("Head turns toward speaker (Phase 4 SSL)."),
    B("Therapist nods on reflective phrases."),
    B("Phase metrics populated in Grafana within 60 s of first turn."),
    PageBreak(),
]

# 19. OPERATOR REFERENCE
story += [
    H1("19. Operator&rsquo;s reference"),
    H2("Branches at-a-glance"),
    kv([
        ["main", "f606534. Production baseline. Untouched by this rework."],
        ["dev/architecture-rework", "79a905c. The rework. 96 commits, 71 files, +26 K LOC."],
        ["research/sage-cbt", "Separate research track. NOT touched by this rework."],
    ]),
    H2("Files to read first if you want to understand the system"),
    B("<code>docs/PRD_v2.md</code> &mdash; the rework spec."),
    B("<code>docs/PHASE_1_TASK_MAP.md</code> &mdash; frame envelope, env vars, phase labels (foundational contracts)."),
    B("<code>server/app_ws.py</code> &mdash; the heart of the new transport."),
    B("<code>nao/wake_state.py</code> &mdash; the wake state machine."),
    B("<code>nao/utils/brain.py</code> &mdash; the local brain cache."),
    B("<code>server/dashboards/README.md</code> &mdash; how to bring up Prometheus + Grafana."),
    H2("Common tasks"),
    H3("Push to GitHub for review"),
    code("git push origin dev/architecture-rework"),
    H3("Test the new transport without touching the robot"),
    code("""
USE_WS=1 ./run.sh server-only
curl http://localhost:5050/health
curl http://localhost:5050/metrics
"""),
    H3("Force the legacy Flask path (rollback)"),
    code("USE_WS=0 ./run.sh"),
    H3("Wipe brain cache on robot"),
    code("ssh nao@172.20.95.127 'rm -f ~/nao_assist/brain.json'"),
    H3("Apply DB migration"),
    code("python -c 'from server.migrations import apply_pending_migrations; apply_pending_migrations()'"),
    H2("Where the agent reports live"),
    P("Each parallel agent committed with the message format <code>[Phase N] &lt;slug&gt;: &lt;summary&gt;</code>. Search the git log:"),
    code("git log --oneline --grep='\\[Phase' main..HEAD"),
    P("Each agent&rsquo;s detailed report (decisions, contract questions, deps declared) lives in the commit body or in their worktree at <code>.claude/worktrees/agent-&lt;hash&gt;/</code>."),
    PageBreak(),
]

# 20. GLOSSARY
story += [
    H1("20. Glossary"),
    kv([
        ["NAOqi", "Aldebaran/SoftBank middleware. Python 2.7 + C++. Provides ALMotion, ALAudioDevice, ALFaceDetection, ALSpeechRecognition, ALLeds, ALAutonomousLife, etc."],
        ["WebSocket", "Long-lived bidirectional TCP stream. Replaces HTTP request/response for the voice loop."],
        ["FastAPI", "Async-first Python web framework. Replaces Flask in this rework."],
        ["uvicorn", "ASGI server that hosts FastAPI."],
        ["VAD", "Voice activity detection. Two layers: energy-based on robot, Silero (neural) on server (authoritative)."],
        ["Silero", "Open-source neural VAD. We use it server-side in streaming mode with adaptive bimodal threshold."],
        ["EoU arbiter", "End-of-utterance arbiter. Combines Silero + robot energy hint + semantic_endpoint to decide a turn is over."],
        ["semantic_endpoint", "GPT-4o-mini single-token Yes/No classifier on transcript-so-far. Async + LRU+TTL cached."],
        ["Realtime API", "OpenAI&rsquo;s WebRTC/WebSocket voice API. Considered, benchmarked, NOT chosen (D1 rationale: model lock-in, audio desync under interruption, 3-5&times; cost, py2.7 can&rsquo;t do WebRTC)."],
        ["WebRTC", "Real-time peer-to-peer protocol with FEC and Opus. Python 2.7 has no working WebRTC client."],
        ["AWARE state", "The intermediate wake state we added. Face detected but NOT engaged. Prevents auto-greeting passersby."],
        ["Engagement gate", "One of 5 signals that promotes AWARE → ENGAGED: mutual gaze, proximity, sustained face, speech onset, keyword."],
        ["BrainCache", "Robot-side 64 KB JSON cache for identity, preferences, prompt fragments. NOT a knowledge base."],
        ["brain_sync", "Server&rarr;robot WS frame that pushes brain.json updates."],
        ["Motion-trigger shortcut", "Regex on transcript that bypasses the LLM for clear body-action commands. Sub-second response."],
        ["Crisis gate", "Hardcoded 988 hotline reply that runs before any agent. LLM cannot override."],
        ["CS Navigator", "Operator&rsquo;s deployed Cloud Run FastAPI for Morgan-CS knowledge. Replaces Pinecone."],
        ["Phase label", "A named stage we time with <code>metrics.phase_timer(label)</code>. 22 labels whitelisted; emit to <code>latency_ms</code> histogram."],
        ["structlog", "Structured JSON logging. Per-turn event with user/session_id/turn_idx/phase_ms/transcript/outcome."],
        ["Prometheus", "Time-series metrics collector. We expose /metrics endpoint with 1 histogram + 8 counters."],
        ["Grafana", "Dashboard tool that queries Prometheus. We ship a 10-panel JSON for import."],
        ["Worktree", "Isolated git checkout. Each parallel agent gets one off dev/architecture-rework."],
        ["Task map", "Per-phase contract document at <code>docs/PHASE_N_TASK_MAP.md</code>. Defines file ownership, public APIs, env vars, latency labels."],
    ]),
    Spacer(1, 0.3 * inch),
    H2("End of walkthrough"),
    P(
        "Drop this PDF into NotebookLM as the primary source. Ask it any of:"
    ),
    B("&ldquo;Walk me through the wake state machine.&rdquo;"),
    B("&ldquo;Why was Realtime API rejected?&rdquo;"),
    B("&ldquo;What does the EoU arbiter do?&rdquo;"),
    B("&ldquo;How does brain_sync work?&rdquo;"),
    B("&ldquo;What&rsquo;s still pending before merge to main?&rdquo;"),
    P("For source-level questions, point NotebookLM at <code>docs/PRD_v2.md</code> + the per-phase task maps as additional sources."),
]


def main():
    doc = SimpleDocTemplate(
        OUT, pagesize=LETTER,
        leftMargin=0.85 * inch, rightMargin=0.85 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title="Nao-OpenAI-Morgan-Assist v2 Architectural Rework",
        author="Aayush Shrestha",
    )

    def _on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(GRAY)
        if doc.page > 1:
            canvas.drawString(0.85 * inch, 0.4 * inch, "Nao-OpenAI-Morgan-Assist  ·  v2 rework walkthrough")
            canvas.drawRightString(LETTER[0] - 0.85 * inch, 0.4 * inch, f"page {doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
