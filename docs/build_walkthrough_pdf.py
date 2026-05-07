"""Generate a comprehensive end-to-end walkthrough PDF for Nao-OpenAI-Morgan-Assist.

Output: docs/Nao_Morgan_Assist_Walkthrough.pdf

Run from repo root:
    python docs/build_walkthrough_pdf.py
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
    Table, TableStyle, KeepTogether,
)


HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "Nao_Morgan_Assist_Walkthrough.pdf")

# ── Styles ────────────────────────────────────────────────────────────
styles = getSampleStyleSheet()

NAVY = HexColor("#0B2545")
ORANGE = HexColor("#F25C05")
GRAY = HexColor("#444")
LIGHT = HexColor("#EEF2F7")

styles.add(ParagraphStyle(
    name="Cover",
    fontName="Helvetica-Bold", fontSize=28, leading=34,
    textColor=NAVY, spaceAfter=12,
))
styles.add(ParagraphStyle(
    name="CoverSub",
    fontName="Helvetica", fontSize=14, leading=18,
    textColor=GRAY, spaceAfter=8,
))
styles.add(ParagraphStyle(
    name="H1",
    fontName="Helvetica-Bold", fontSize=20, leading=24,
    textColor=NAVY, spaceBefore=18, spaceAfter=10,
))
styles.add(ParagraphStyle(
    name="H2",
    fontName="Helvetica-Bold", fontSize=14, leading=18,
    textColor=ORANGE, spaceBefore=12, spaceAfter=6,
))
styles.add(ParagraphStyle(
    name="H3",
    fontName="Helvetica-Bold", fontSize=11, leading=14,
    textColor=NAVY, spaceBefore=8, spaceAfter=4,
))
styles.add(ParagraphStyle(
    name="Body",
    fontName="Helvetica", fontSize=10.5, leading=15,
    textColor=HexColor("#222"), alignment=TA_JUSTIFY, spaceAfter=6,
))
styles.add(ParagraphStyle(
    name="MyBullet",
    fontName="Helvetica", fontSize=10.5, leading=14,
    textColor=HexColor("#222"), leftIndent=14, bulletIndent=2, spaceAfter=2,
))
styles.add(ParagraphStyle(
    name="Note",
    fontName="Helvetica-Oblique", fontSize=9.5, leading=13,
    textColor=GRAY, leftIndent=10, spaceAfter=6,
))
CODE_STYLE = ParagraphStyle(
    name="Code",
    fontName="Courier", fontSize=8.8, leading=11,
    textColor=HexColor("#1a1a1a"), leftIndent=8, spaceAfter=8,
    backColor=LIGHT, borderPadding=4,
)


def H1(t): return Paragraph(t, styles["H1"])
def H2(t): return Paragraph(t, styles["H2"])
def H3(t): return Paragraph(t, styles["H3"])
def P(t): return Paragraph(t, styles["Body"])
def B(t): return Paragraph("• " + t, styles["MyBullet"])
def NOTE(t): return Paragraph(t, styles["Note"])
def code(t): return Preformatted(t.strip("\n"), CODE_STYLE)


def kv_table(rows, col_widths=(1.6 * inch, 4.4 * inch)):
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


# ── Build story ───────────────────────────────────────────────────────
story = []

# COVER
story += [
    Spacer(1, 1.6 * inch),
    Paragraph("Nao + OpenAI", styles["Cover"]),
    Paragraph("Morgan State Robot Assistant", styles["Cover"]),
    Spacer(1, 0.2 * inch),
    Paragraph(
        "End-to-end system walkthrough &mdash; architecture, code, models, voice pipeline, "
        "vision, multi-agent routing, deployment, and operator&rsquo;s guide.",
        styles["CoverSub"],
    ),
    Spacer(1, 0.5 * inch),
    Paragraph("<b>Author:</b> Aayush Shrestha (aashr3@morgan.edu)", styles["CoverSub"]),
    Paragraph("<b>Repo:</b> github.com/theaayushstha1/Nao-OpenAI-Morgan-Assist", styles["CoverSub"]),
    Paragraph("<b>Generated:</b> 2026-05-06", styles["CoverSub"]),
    PageBreak(),
]

# TOC
story += [
    H1("Contents"),
    B("1. What is this project"),
    B("2. System architecture (one-page diagram)"),
    B("3. Hardware &amp; network setup"),
    B("4. The conversation lifecycle (wake to speak)"),
    B("5. Voice pipeline: VAD, ASR, LLM, TTS, playback"),
    B("6. The OpenAI Agents SDK graph (router, chat, chatbot, skills, therapist)"),
    B("7. Tools the agents can call (NAO actions, RAG, emotion)"),
    B("8. Motion-trigger shortcut (LLM bypass)"),
    B("9. Safety gate &amp; therapist sub-agents (CBT, grounding)"),
    B("10. Vision / camera / face recognition / onboarding"),
    B("11. Persistence: SQLite session, recaps, user cache"),
    B("12. Configuration files &amp; environment variables"),
    B("13. Deployment: run.sh and the rsync workflow"),
    B("14. Operator&rsquo;s guide: how to actually use the robot"),
    B("15. File-by-file reference"),
    B("16. Troubleshooting &amp; gotchas"),
    B("17. Glossary"),
    PageBreak(),
]

# 1. WHAT
story += [
    H1("1. What is this project"),
    P(
        "This is a humanoid voice assistant built on the <b>SoftBank/Aldebaran NAO V6</b> robot "
        "for Morgan State University. The robot can chat naturally, answer questions about "
        "Morgan&rsquo;s CS program from a Pinecone RAG index, run a therapy/CBT mode with "
        "grounding exercises, perform body actions (wave, dance, nod), and recognise users by face."
    ),
    P(
        "The robot itself runs <b>Python 2.7</b> (because that is what naoqi ships with). All the "
        "smart stuff lives on a separate Python 3.11+ Flask server which talks to OpenAI. The "
        "robot is essentially a microphone, camera, speaker, and motor controller; the brain is in "
        "the cloud. Each user turn travels: <i>NAO &rarr; Flask &rarr; OpenAI Agents SDK &rarr; "
        "tools &rarr; back to NAO</i>, with audio (MP3) and a list of body-action commands."
    ),
    H2("Why this split?"),
    B("naoqi is locked to Python 2.7 &mdash; you cannot run modern OpenAI SDKs on the robot."),
    B("Heavy lifting (Whisper, GPT-4o, embeddings) needs CPython 3.11+ and decent memory."),
    B("Latency is acceptable because the only round-trip is one HTTP POST per user turn."),
    B("It also lets you swap models or add tools without ever touching the robot."),
    H2("Models &amp; services in play"),
    kv_table([
        ["LLM (router + agents)", "OpenAI gpt-4o (Agents SDK 0.13.6)"],
        ["Vision", "gpt-4o (multimodal &mdash; same model, image attached)"],
        ["ASR (speech-to-text)", "OpenAI gpt-4o-mini-transcribe (Whisper family)"],
        ["TTS (text-to-speech)", "OpenAI tts-1, voice = nova, MP3"],
        ["Audio amplifier", "ffmpeg volume= +16 dB filter on TTS bytes"],
        ["RAG", "Pinecone serverless index (Morgan CS knowledge base)"],
        ["Session store", "SQLite (server/nao.db) via SDK SQLiteSession"],
        ["Crisis hotline", "Hardcoded 988 reply &mdash; LLM cannot override"],
    ]),
    PageBreak(),
]

# 2. ARCHITECTURE
story += [
    H1("2. System architecture"),
    P("One picture, plain text:"),
    code("""
            ┌──────────────────────── NAO ROBOT (Python 2.7) ─────────────────────────┐
            │                                                                         │
            │  main.py ─► wake_listener (ALSpeechRecognition: "hey nao")              │
            │       │                                                                 │
            │       ▼                                                                 │
            │  conversation.py  (single mode loop)                                    │
            │       │ 1) audio_handler.record_voice()  ── VAD, energy-based           │
            │       │ 2) camera_capture.snap_quick()   ── 1 JPEG / turn               │
            │       │ 3) POST /stream_turn  (multipart: WAV + JPEG + form fields)     │
            │       │ 4) parse SSE: {audio b64, action, done}                         │
            │       │ 5) stream_tts.play_mp3_b64()  +  utils.nao_execute.dispatch()   │
            │       └──► repeat until exit_detection or silence cap                   │
            └─────────────────────────────────────┬──────────────────────────────────┘
                                                  │  WiFi (172.20.95.x /24)
                                                  ▼
            ┌─────────────────── FLASK SERVER (Python 3.11+) ────────────────────────┐
            │                                                                        │
            │  POST /stream_turn  (server.py)                                        │
            │       │                                                                │
            │       ▼                                                                │
            │  safety.crisis_check()  ── keyword + LLM. If crisis: 988 → return.     │
            │       │                                                                │
            │       ▼                                                                │
            │  motion_trigger.detect(transcript)                                     │
            │       │  match? ─► emit OpenAI TTS ack + action  ─► done.              │
            │       │  no match                                                      │
            │       ▼                                                                │
            │  Runner.run(router_agent, message, session, ctx)                       │
            │       │                                                                │
            │       │  router (gpt-4o)  ── handoff to one of:                        │
            │       │    chat ── general convo + body actions                        │
            │       │    chatbot ── Pinecone RAG for Morgan CS                       │
            │       │    skills ── time, weather, timers, todos                      │
            │       │    therapist ── empathic + CBT/grounding handoffs              │
            │       │                                                                │
            │       │  tools fire → ctx.actions_queue gets {name, args}              │
            │       ▼                                                                │
            │  openai_tts.synthesize(text)  +  ffmpeg +16 dB                         │
            │       │                                                                │
            │       ▼                                                                │
            │  SSE stream:  audio b64 → action* → done                               │
            └────────────────────────────────────────────────────────────────────────┘
"""),
    PageBreak(),
]

# 3. HARDWARE
story += [
    H1("3. Hardware &amp; network setup"),
    H2("The robot"),
    kv_table([
        ["Model", "NAO V6 (SoftBank/Aldebaran)"],
        ["OS / SDK", "NAOqi 2.8 (Linux), Python 2.7"],
        ["Sensors used", "2x mic array, top RGB camera, sonar, joint encoders"],
        ["Output", "speaker (left ear), 25 actuators, RGB LEDs (eyes, ears, chest)"],
        ["IP", "172.20.95.127 (DHCP &mdash; ask Morgan IT for a reservation)"],
        ["Hostname", "nao.local (mDNS fallback)"],
        ["SSH user", "nao (password in .env, never commit)"],
    ]),
    H2("Your Mac (the brain)"),
    kv_table([
        ["Role", "Hosts the Flask server on port 5050"],
        ["Network", "Same WiFi as NAO (172.20.95.0/24 on Morgan CS network)"],
        ["IP detection", "run.sh auto-picks first ifconfig IPv4 on the NAO subnet"],
        ["Required tools", "Python 3.11, ffmpeg, rsync, ssh"],
    ]),
    H2("Wiring it up"),
    code("""
# 1. Power on the robot, wait for "Gnuk gnuk" boot chime + steady eyes.
# 2. Confirm reachability from your Mac:
ping -c 3 172.20.95.127

# 3. One-time SSH key (so the robot doesn't ask for a password every deploy):
ssh-copy-id nao@172.20.95.127

# 4. Verify the Mac's Python deps:
cd ~/Projects/Nao-OpenAI-Morgan-Assist
pip install -r server/requirements.txt
brew install ffmpeg

# 5. Fill .env (see Section 12), then:
./run.sh
"""),
    PageBreak(),
]

# 4. LIFECYCLE
story += [
    H1("4. The conversation lifecycle"),
    P(
        "From the user&rsquo;s perspective: <i>say &ldquo;hey nao&rdquo; &rarr; talk &rarr; robot replies &amp; "
        "moves &rarr; talk again &rarr; say &ldquo;goodbye&rdquo;.</i> Under the hood it is a state machine:"),
    H2("State 1 &mdash; Idle / Wake"),
    P(
        "<code>nao/main.py</code> starts <code>wake_listener.py</code>, which arms NAOqi&rsquo;s "
        "<code>ALSpeechRecognition</code> with the phrase list <i>{&ldquo;hey nao&rdquo;, &ldquo;ok nao&rdquo;, &ldquo;hi nao&rdquo;}</i>. "
        "The robot does nothing else here &mdash; cheap, on-device wake word."
    ),
    H2("State 2 &mdash; Onboarding (only if user is unknown)"),
    P(
        "<code>conversation._resolve_username()</code> first checks the in-memory "
        "<code>_USER_CACHE</code> (cross-wake persistence), then the on-disk "
        "<code>~/nao_assist/user_cache.json</code>, then runs <code>face_naoqi.recognise()</code>. "
        "If still unknown, it greets the user with one combined prompt "
        "(&ldquo;Hey there. Before we get going, what should I call you? Just look at me when you say it.&rdquo;) "
        "and starts a parallel face-learning thread while recording the answer. "
        "<code>name_utils.extract_name()</code> handles &ldquo;my name is X&rdquo;, &ldquo;name is X&rdquo;, &ldquo;I go by X&rdquo;, "
        "&ldquo;just call me X&rdquo;, plus bare one-word names."
    ),
    H2("State 3 &mdash; Turn loop"),
    P("Each turn runs in <code>conversation._handle_turn()</code>:"),
    B("<b>Record</b>: <code>audio_handler.record_voice()</code> with energy VAD &mdash; calibrates 800 ms ambient, then waits for energy &gt; <code>start_th</code>."),
    B("<b>Snap</b>: <code>camera_capture.snap_quick()</code> grabs a 320&times;240 JPEG (gated by <code>IMAGE_PER_TURN</code>)."),
    B("<b>POST</b>: multipart to <code>http://&lt;mac&gt;:5050/stream_turn</code> with <i>audio</i>, <i>image</i>, <i>username</i>, <i>hint</i>, <i>asking_name</i>."),
    B("<b>Stream</b>: server returns SSE events &mdash; <i>partial</i>, <i>audio</i>, <i>action</i>, <i>done</i>."),
    B("<b>Speak + move</b>: <code>stream_tts.play_mp3_b64()</code> plays the OpenAI MP3; <code>nao_execute.dispatch()</code> fires the action calls."),
    B("<b>Loop guard</b>: <code>exit_detection.is_exit_intent()</code> watches for &ldquo;goodbye/that&rsquo;ll be all/stop&rdquo;; otherwise records again."),
    H2("State 4 &mdash; Crash recovery"),
    P(
        "If anything throws, <code>main.py</code> stops <code>ALAudioRecorder</code> + "
        "<code>ALAudioPlayer</code>, sleeps 2 s (lets NAOqi services settle), then re-arms the wake "
        "listener. We learned this the hard way &mdash; you used to get three concurrent "
        "<code>main.py</code> processes fighting for the mic."
    ),
    PageBreak(),
]

# 5. VOICE
story += [
    H1("5. Voice pipeline"),
    H2("5.1 Recording &amp; VAD (NAO side, audio_handler.py)"),
    P(
        "We use a pure-Python energy VAD because <code>webrtcvad</code> on Python 2.7 is a pain. "
        "After 800 ms of ambient calibration we compute four thresholds:"
    ),
    code("""
start_th  = max(ambient + 380, 700)   # to begin recording at all
keep_th   = max(ambient + 250, 420)   # to stay in SPEECH state
soft_th   = max(ambient + 120, 350)   # gray zone (QUIET state)
silent_th = max(ambient + 30,  260)   # below this = SILENT

# Three-tier post-onset state machine:
#   SPEECH   ── energy >= keep_th          (user is talking)
#   QUIET    ── silent_th <= e < keep_th    (between words)
#   SILENT   ── energy <  silent_th         (real silence)
#
# End-of-utterance:
#   - leave SPEECH with TRAIL_MS=500 of QUIET → start GRACE_MS=300
#   - if any speech returns during grace, reset
#   - GRACE_MS expires AND last 600 ms is SILENT → stop & return
"""),
    P(
        "Hard caps prevent runaway captures: <code>SPEECH_MAX_SEC = 10.0</code>, "
        "<code>DEFAULT_MAX_SEC = 12.0</code>, <code>MAX_QUIET_AFTER_SPEECH_S = 0.5</code>. We disabled "
        "the client-side <code>_trim_silence</code> because it was shaving off the first word."
    ),
    H2("5.2 Transcription (server, _transcribe)"),
    P(
        "We tried Deepgram Nova-2 &mdash; it was slow on the CS network, so we switched to OpenAI&rsquo;s "
        "<code>gpt-4o-mini-transcribe</code>. Same call signature, lower latency, and it handles "
        "the messy NAO mic audio well. Deepgram code is still there behind <code>USE_DEEPGRAM=0</code> "
        "in case you want to A/B."
    ),
    H2("5.3 Text rejection guards"),
    P("Before the agent ever sees the transcript we run a chain of cheap filters:"),
    B("<code>_validate_wav</code> &mdash; reject 0-byte / corrupt WAV."),
    B("<code>_has_voice</code> &mdash; webrtcvad on the server side as a second opinion (voiced ratio &gt;= 0.18)."),
    B("<code>_is_robot_named_echo</code> + <code>_looks_like_robot_greeting_echo</code> &mdash; catch &ldquo;hey there&rdquo; type self-echo."),
    B("<code>_looks_like_hallucination</code> &mdash; reject Whisper&rsquo;s favourite ghosts (&ldquo;you&rdquo;, &ldquo;thank you.&rdquo;, single short noise tokens)."),
    B("<code>_is_self_echo</code> &mdash; bigram overlap with last TTS reply &gt; 60% = drop."),
    NOTE("When <code>asking_name=true</code>, the hallucination filter is bypassed so &ldquo;Max&rdquo; or &ldquo;Eve&rdquo; gets through."),
    H2("5.4 Partial buffer"),
    P(
        "If a turn rejects (e.g. &ldquo;you&rdquo;), we do not throw the audio away &mdash; we stash the text in a "
        "per-user partial buffer and merge it with the next non-rejected turn. <code>_PARTIAL_MAX_WAIT=3</code> "
        "caps how long we wait before giving up and just sending what we have. This is what fixed "
        "the &ldquo;robot only hears every other sentence&rdquo; bug."
    ),
    H2("5.5 TTS &amp; playback"),
    code("""
# server/openai_tts.py
def synthesize(text):
    mp3 = client.audio.speech.create(
        model="tts-1", voice="nova", response_format="mp3", input=text,
    ).read()
    return _amplify_mp3(mp3, gain_db=16)   # ffmpeg pipe

# nao/stream_tts.py
def play_mp3_b64(b64):
    audio.setOutputVolume(100)             # re-pin every play
    audio.setMasterVolume(1.0)
    path = "/tmp/nao_tts_%d.mp3" % time.time()
    open(path, "wb").write(base64.b64decode(b64))
    player.playFile(path)                  # ALAudioPlayer (NAOqi)
"""),
    P(
        "Three pain points we fixed: (1) NAO&rsquo;s onboard <code>ALTextToSpeech</code> was firing alongside "
        "the OpenAI MP3 &mdash; suppressed via the <code>got_audio</code> flag in <code>stream_tts</code>. "
        "(2) Volume was getting silently dropped by some service between sentences &mdash; we now re-pin "
        "<code>setOutputVolume(100)</code> on every play. (3) Even at 100 the speaker peaked low, so we "
        "added a +16 dB ffmpeg gain on the server side."
    ),
    PageBreak(),
]

# 6. AGENTS GRAPH
story += [
    H1("6. The Agents SDK graph"),
    P(
        "We use <code>openai-agents 0.13.6</code>. The graph is wired up in <code>server/agents/</code>:"
    ),
    code("""
                    ┌──────── router ────────┐  (triage agent)
                    │   gpt-4o, no tools     │
                    │   instructions: pick   │
                    │   one of 4 handoffs    │
                    └──┬───────┬──────┬──────┘
                       │       │      │
        ┌──────────────┘       │      └────────────────┐
        ▼                      ▼                       ▼
    chat             chatbot (Morgan CS RAG)      skills           therapist
    ── 18 NAO        ── pinecone_search tool      ── time,         ── empathic chat
       action tools                                  weather,         ── CBT/grounding
                                                     timers,             handoffs
                                                     todos
                                                                  ↓
                                                            cbt_coach   grounding_coach
"""),
    H2("Handoff hints"),
    P(
        "The wake listener can pre-route by listening for keywords AFTER &ldquo;hey nao&rdquo;: "
        "&ldquo;hey nao chat mode&rdquo; sets <code>hint=chat</code>, &ldquo;hey nao therapy&rdquo; sets "
        "<code>hint=therapy</code>. The router still has the final say but biases toward the hint."
    ),
    H2("Per-agent purpose"),
    kv_table([
        ["router.py", "Cheap triage. No tools. Output is just a handoff target."],
        ["chat.py", "Default chitchat. Has the full 18-tool NAO action toolkit."],
        ["chatbot.py", "Morgan CS knowledge agent. Calls pinecone_search."],
        ["skills.py", "Practical utilities &mdash; clock, weather (stub), kitchen timers, todos."],
        ["therapist.py", "Warm reflective listener. Can do body actions (10 of them) to lighten the room."],
        ["cbt_coach.py", "Walks a thought record: situation &rarr; thought &rarr; emotion &rarr; reframe."],
        ["grounding_coach.py", "5-4-3-2-1, box breathing, body scan."],
        ["mi_coach.py", "Motivational interviewing turn (experimental, off the main path)."],
    ]),
    H2("Memory between turns"),
    P(
        "<code>session.SQLiteSession(thread_id=username)</code> persists the entire transcript per user "
        "in <code>server/nao.db</code>. The Agents SDK transparently injects prior turns into each "
        "<code>Runner.run()</code>. Long sessions are summarised by <code>memory_rollup.py</code> and the "
        "summary is replayed at the top of new sessions (so the robot &ldquo;remembers&rdquo; you across days)."
    ),
    PageBreak(),
]

# 7. TOOLS
story += [
    H1("7. Tools the agents can call"),
    H2("7.1 NAO actions (server/tools/nao_actions.py)"),
    P(
        "Each tool is a thin <code>@function_tool</code> that just appends "
        "<code>{name, args}</code> to <code>ctx.actions_queue</code>. The server reads the queue after "
        "<code>Runner.run()</code> finishes and ships it to the robot. The robot dispatches via "
        "<code>nao/utils/nao_execute.py</code>, which maps each name to a real NAOqi call."
    ),
    kv_table([
        ["Posture", "stand_up, sit_down, kneel"],
        ["Gestures", "wave_hand, wave_both_hands, nod_head, shake_head, clap_hands"],
        ["Locomotion", "move_forward, move_backward, turn_left, turn_right, spin"],
        ["Performance", "dance, follow_movement, stop_follow, play_animation"],
        ["LEDs", "change_eye_color, set_led_color"],
    ]),
    H2("7.2 Pinecone RAG (server/tools/pinecone_search.py)"),
    P(
        "Single tool: <code>pinecone_search(query: str, top_k: int = 4) -&gt; list[str]</code>. The "
        "knowledge base is the Morgan State CS website + course catalogue, chunked and embedded with "
        "<code>text-embedding-3-small</code>. Index name is <code>morgan-cs</code>."
    ),
    H2("7.3 Emotion / vision (server/tools/emotion.py)"),
    P("Six tools the therapist actually uses:"),
    B("<code>observe_face</code> &mdash; reads the latest JPEG from context, asks gpt-4o for affect."),
    B("<code>log_emotion</code> &mdash; persists the reading on the session for the recap."),
    B("<code>identify_distortion</code> &mdash; CBT cognitive-distortion classifier."),
    B("<code>suggest_reframe</code> &mdash; produces a balanced thought."),
    B("<code>set_camera_consent</code> &mdash; toggles the per-user flag in <code>user_prefs</code>."),
    B("<code>recap_session</code> &mdash; writes a short paragraph at end of therapy mode."),
    H2("7.4 Skills (server/tools/skills_tools.py)"),
    B("<code>get_time</code>, <code>get_date</code> &mdash; trivial."),
    B("<code>set_timer(minutes, label)</code> &mdash; in-memory, fires a callback that <code>play_animation('happy')</code>s when done."),
    B("<code>add_todo</code>, <code>list_todos</code>, <code>complete_todo</code> &mdash; SQLite-backed per user."),
    PageBreak(),
]

# 8. MOTION TRIGGER
story += [
    H1("8. Motion-trigger shortcut"),
    P(
        "The router was unreliable for clear physical commands &mdash; it would hand off to a generic "
        "chat agent that replied &ldquo;I&rsquo;m a virtual assistant, I can&rsquo;t stand up.&rdquo; Even with the "
        "right toolset wired up, sometimes the LLM refused. So we added a deterministic short-circuit:"
    ),
    code("""
# server/motion_trigger.py  (runs BEFORE Runner.run)
_TRIGGERS = [
    ("stand_up",     {},                  "Standing up.",       ["stand up", "get up", ...]),
    ("sit_down",     {},                  "Sitting down.",      ["sit down", ...]),
    ("wave_hand",    {"hand": "right"},   "Waving hi!",         ["wave at me", "say hi", ...]),
    ("dance",        {"style": "robot"},  "Let's dance!",       ["do a dance", ...]),
    ("change_eye_color", {"color":"red"}, "Eyes red.",          ["eyes red", ...]),
    # ...18 more entries
]
# Each phrase gets a word-boundary regex; first match wins.

def detect(transcript): ...   # → MotionMatch | None
"""),
    P(
        "If <code>detect()</code> returns a match, <code>/stream_turn</code> emits the OpenAI TTS ack + "
        "the action + a <code>done</code> event, and never even invokes the agent graph. Latency drops "
        "from ~2.5 s to ~0.6 s on motion commands. Non-motion text (&ldquo;what is the weather?&rdquo;) "
        "returns <code>None</code> and falls through to the LLM as normal."
    ),
    NOTE(
        "Order matters: longer phrases come first so &ldquo;sit down&rdquo; doesn&rsquo;t accidentally match "
        "&ldquo;sit-down comedy.&rdquo; Word-boundary regexes (<code>\\b...\\b</code>) prevent &ldquo;withstand&rdquo; "
        "from firing the &ldquo;stand&rdquo; trigger."
    ),
    PageBreak(),
]

# 9. SAFETY + THERAPIST
story += [
    H1("9. Safety gate &amp; therapist sub-agents"),
    H2("9.1 Crisis check (server/safety.py)"),
    P(
        "Runs before <i>any</i> agent sees the user message. Two layers: (1) a fast keyword set "
        "(&ldquo;kill myself&rdquo;, &ldquo;end it&rdquo;, &ldquo;suicide&rdquo;, &ldquo;hurt myself&rdquo;, etc.) and (2) a small "
        "LLM classifier (&ldquo;is this an active crisis?&rdquo;) for ambiguity. If either fires, the response "
        "is a <b>hardcoded</b> 988 hotline message &mdash; no LLM in the loop. The agent graph is "
        "<i>not</i> consulted. This is non-negotiable: an LLM cannot &ldquo;decide&rdquo; not to share 988."
    ),
    H2("9.2 Therapist agent"),
    P(
        "Tone target: warm, reflective, short turns, asks one question at a time. We added an "
        "explicit instruction to <i>actually call</i> the body-action tools when the user asks for "
        "movement &mdash; the default LLM behaviour was &ldquo;I&rsquo;m a virtual assistant, I can&rsquo;t move.&rdquo; "
        "<code>THERAPIST_ACTIONS</code> exposes 10 tools: dance, wave (one + both), clap, nod, shake, "
        "stand, sit, follow, set_led_color."
    ),
    H2("9.3 Sub-agents"),
    B("<b>cbt_coach</b>: walks a thought record one cell at a time. Tool: <code>identify_distortion</code> + <code>suggest_reframe</code>. Hands back to therapist when done."),
    B("<b>grounding_coach</b>: drives 5-4-3-2-1 (sight/sound/touch), box breathing (4-4-4-4 pattern), or a 60s body scan. Stateful within the run."),
    B("<b>mi_coach</b>: experimental motivational-interviewing branch (open/affirm/reflect/summarise). Off the main router by default."),
    PageBreak(),
]

# 10. VISION
story += [
    H1("10. Vision, camera, face recognition, onboarding"),
    H2("10.1 Per-turn JPEG"),
    P(
        "When <code>IMAGE_PER_TURN=1</code>, every turn captures one 320&times;240 JPEG via "
        "<code>nao/utils/camera_capture.py:snap_quick()</code> and POSTs it alongside the audio. "
        "The therapist&rsquo;s <code>observe_face</code> tool reads it from context and sends it to "
        "gpt-4o (multimodal) with the prompt &ldquo;describe affect, eye contact, posture in &lt; 30 words.&rdquo;"
    ),
    H2("10.2 Camera consent"),
    P(
        "Per-user opt-in stored in SQLite (<code>user_prefs</code> table). The therapist tool "
        "<code>set_camera_consent(value: bool)</code> flips it. When false, the server sets "
        "<code>suppress_image=true</code> on responses and the robot does not include images in the next "
        "POST."
    ),
    H2("10.3 Face recognition (NAOqi-native)"),
    P(
        "We use <code>ALFaceDetection</code>&rsquo;s built-in learner instead of running our own CV. "
        "<code>face_naoqi.py</code> provides three calls:"
    ),
    B("<code>recognise(timeout)</code> &mdash; returns the learned name or None."),
    B("<code>learn_new_face_naoqi(name, timeout=4)</code> &mdash; silent learn (no &ldquo;please look at me&rdquo; prompt). Runs in a thread parallel to the name-asking flow."),
    B("<code>clear_db()</code> &mdash; nukes everything. Used when you have ghost users."),
    H2("10.4 The onboarding flow"),
    P("This was the most-iterated piece. Settled flow:"),
    code("""
# nao/conversation.py:_resolve_username

1.  if username in _USER_CACHE:                 return it
2.  if disk has user_cache.json with face_id:   hydrate, return
3.  face = face_naoqi.recognise(timeout=2)
    if face and face != "unknown":              cache + return
4.  start face-learning thread for "guest"     # parallel
    say  "Hey there. Before we get going,
          what should I call you? Just look at me when you say it."
    record + transcribe with asking_name=true   # short-name guard off
    name = name_utils.extract_name(transcript)
    join face thread → relabel from "guest" → name
    cache to _USER_CACHE + disk
    say  "Nice to meet you, NAME."  (or "Welcome back, NAME." if step 3 hit)
"""),
    NOTE(
        "Old flow asked for the name three or four times because each new wake phrase re-instantiated "
        "<code>conversation.py</code> with empty state. Module-level <code>_USER_CACHE</code> + disk "
        "cache fixed the &ldquo;asked me 20 times&rdquo; bug."
    ),
    PageBreak(),
]

# 11. PERSISTENCE
story += [
    H1("11. Persistence"),
    H2("11.1 Server-side (SQLite, server/nao.db)"),
    kv_table([
        ["agent_sessions (SDK)", "Full transcript per thread_id (= username). Auto-managed."],
        ["user_prefs", "camera_consent BOOL, learned_face_id TEXT, last_seen TIMESTAMP."],
        ["recaps", "Therapy-mode session summaries; replayed at session start."],
        ["todos", "{user, text, status, created_at} for the skills agent."],
        ["emotion_log", "Per-turn affect notes from observe_face (used for recap)."],
    ]),
    H2("11.2 NAO-side cache (~/nao_assist/user_cache.json)"),
    P(
        "Tiny JSON dictionary written by <code>nao/utils/user_cache.py</code>. Survives across "
        "<code>main.py</code> restarts so the robot doesn&rsquo;t re-onboard you when you re-trigger "
        "the wake word the next morning."
    ),
    PageBreak(),
]

# 12. CONFIG
story += [
    H1("12. Configuration &amp; environment"),
    H2(".env (gitignored, lives at repo root)"),
    code("""
# OpenAI
OPENAI_API_KEY=sk-proj-...
OPENAI_TTS_VOICE=nova
OPENAI_TTS_MODEL=tts-1
OPENAI_TTS_GAIN_DB=16
USE_OPENAI_TTS=1

# Speech-to-text
USE_DEEPGRAM=0                  # we ditched it; Whisper is faster
DEEPGRAM_API_KEY=...            # kept in case you re-enable

# Pinecone (Morgan CS RAG)
PINECONE_API_KEY=...
PINECONE_INDEX=morgan-cs

# NAO connection
NAO_IP=172.20.95.127
NAO_PASSWORD=...                # never commit
NAO_SHARED_SECRET=...           # HMAC-ish header to lock the server to this robot

# Server
SERVER_PORT=5050
SERVER_IP=auto                  # run.sh detects from ifconfig

# Behaviour flags
IMAGE_PER_TURN=1                # snap a JPEG each turn
USE_SEMANTIC_ENDPOINT=0         # experimental endpointing model, off
"""),
    H2("server/config.py"),
    P("Reads .env and exposes typed constants. No secrets are hardcoded; everything is overrideable from env."),
    H2("nao/config.py"),
    P(
        "Reads two env vars only: <code>SERVER_IP</code> and <code>SERVER_PORT</code>. The Mac side of "
        "<code>run.sh</code> exports them into the SSH session that launches <code>main.py</code> on "
        "the robot, so the robot always knows where to POST."
    ),
    PageBreak(),
]

# 13. DEPLOY
story += [
    H1("13. Deployment: run.sh"),
    P("One-shot launcher. Idempotent. Cleans up after itself. Subcommands:"),
    kv_table([
        ["./run.sh", "Default. Validate env, rsync, kill old robot processes, start server, launch robot, tail logs."],
        ["./run.sh deploy-only", "Just rsync to the robot. Does not start anything."],
        ["./run.sh server-only", "Just start the local Flask server."],
        ["./run.sh stop", "Kill local server + remote main.py + tail processes."],
    ]),
    H2("What run.sh actually does"),
    code("""
1. source .env into shell                          # set -a / set +a
2. validate keys                                   # reject PASTE_* placeholders
3. detect_local_ip()                               # ifconfig | grep on NAO subnet
4. lsof -ti :5050 | xargs kill -9                  # free the port
5. ssh nao@$NAO_IP 'pkill -f main.py'              # no zombie sessions
6. rsync -avz --delete \\
        --exclude '*.pyc' --exclude '__pycache__' \\
        nao/ nao@$NAO_IP:/home/nao/nao_assist/     # never ship stale .pyc
7. ssh nao@$NAO_IP 'find /home/nao/nao_assist -name "*.pyc" -delete'
8. start Flask:    python -m server.server > logs/server.log 2>&1 &
9. wait for /health to return 200
10. ssh nao@$NAO_IP 'SERVER_IP=$LOCAL_IP SERVER_PORT=5050 \\
                     IMAGE_PER_TURN=1 NAO_SHARED_SECRET=... \\
                     python /home/nao/nao_assist/main.py' &
11. tail -f logs/server.log logs/nao.log           # side-by-side, prefixed
"""),
    NOTE(
        "We kill <code>*.pyc</code> on the robot every deploy because NAOqi was happily running stale "
        "bytecode after we updated the <code>.py</code>. Lost an hour debugging a fix that &ldquo;wasn&rsquo;t "
        "deployed&rdquo; that absolutely was."
    ),
    PageBreak(),
]

# 14. OPERATOR'S GUIDE
story += [
    H1("14. Operator&rsquo;s guide &mdash; how to actually use the robot"),
    H2("First-time setup"),
    code("""
git clone https://github.com/theaayushstha1/Nao-OpenAI-Morgan-Assist.git
cd Nao-OpenAI-Morgan-Assist
cp .env.example .env       # then fill in keys
pip install -r server/requirements.txt
brew install ffmpeg

ssh-copy-id nao@172.20.95.127     # one time

./run.sh                   # boots everything; Ctrl-C to stop tailing
"""),
    H2("Demo script (what to actually say)"),
    kv_table([
        ["&ldquo;Hey nao&rdquo;", "Wake. Eyes go to listening blue."],
        ["&ldquo;Hey nao, chat mode&rdquo;", "Wake + bias router toward general chat."],
        ["&ldquo;Hey nao, therapy&rdquo;", "Wake + bias toward therapist agent (CBT/grounding available)."],
        ["&ldquo;Hey nao, skills&rdquo;", "Wake + bias toward time/timer/todo skills."],
        ["&ldquo;Hey nao, morgan&rdquo;", "Wake + bias toward Morgan CS knowledge agent."],
    ]),
    H2("Things to demo"),
    B("<b>Body actions</b>: &ldquo;stand up&rdquo;, &ldquo;wave at me&rdquo;, &ldquo;do a dance&rdquo;, &ldquo;eyes red&rdquo;, &ldquo;spin around&rdquo;. These hit the motion-trigger shortcut &mdash; sub-second response."),
    B("<b>Knowledge</b>: &ldquo;what classes does Morgan&rsquo;s CS department offer?&rdquo; &rarr; chatbot agent + Pinecone."),
    B("<b>Vision</b>: &ldquo;how do I look today?&rdquo; in therapy mode &rarr; observe_face on the JPEG."),
    B("<b>Memory</b>: tell it your favourite colour, end the session, restart, ask &ldquo;remember my favourite colour?&rdquo;"),
    B("<b>Crisis test</b>: do <i>not</i> demo this in public. Internally test &ldquo;I want to hurt myself&rdquo; &rarr; 988 hotline."),
    H2("Ending a session"),
    P(
        "Say &ldquo;goodbye&rdquo;, &ldquo;that&rsquo;ll be all&rdquo;, &ldquo;stop&rdquo;, &ldquo;exit&rdquo;, or &ldquo;thanks bye&rdquo;. "
        "<code>exit_detection.is_exit_intent()</code> closes the loop and the robot returns to wake-word standby."
    ),
    H2("Daily debugging cheats"),
    B("Robot not responding to wake: <code>ssh nao@... 'ps aux | grep main.py'</code> &mdash; should be exactly one process."),
    B("Robot speaks but no movement: check <code>logs/server.log</code> for &ldquo;[motion-trigger]&rdquo; or &ldquo;actions_queue&rdquo; entries."),
    B("Server returns 500: open <code>logs/server.log</code>; usually OpenAI timeout or Pinecone auth."),
    B("Echo / robot hearing itself: bump <code>silent_th</code> in <code>audio_handler.py</code> +50."),
    B("Whisper hallucinates &ldquo;Thanks for watching!&rdquo;: this is the Whisper YouTube ghost. <code>_looks_like_hallucination</code> usually catches it; if not, add the phrase to the deny list."),
    PageBreak(),
]

# 15. FILE-BY-FILE
story += [
    H1("15. File-by-file reference"),
    H2("nao/ (Python 2.7, lives on the robot)"),
    kv_table([
        ["main.py", "Entry point. Pin volume, arm wake listener, crash-recover loop."],
        ["wake_listener.py", "ALSpeechRecognition wake words + extract_hint()."],
        ["conversation.py", "The turn loop. Onboarding, recording, POST, playback, dispatch."],
        ["audio_handler.py", "Energy VAD with calibration, three-tier state machine, end-of-utterance grace."],
        ["camera_capture.py", "ALVideoDevice subscribe + snap_quick() per-turn JPEG."],
        ["face_naoqi.py", "ALFaceDetection wrappers: recognise, learn, clear_db."],
        ["ask_name_utils.py", "Round-trips audio to /turn with asking_name=true."],
        ["name_utils.py", "Regex extractor for &ldquo;my name is X&rdquo; and friends."],
        ["exit_detection.py", "Regex of exit phrases; called every turn."],
        ["nao_execute.py", "Dispatch table from action {name, args} to NAOqi calls."],
        ["stream_tts.py", "Plays MP3 b64 chunks; pins volume per play."],
        ["processing_announcer.py", "(disabled) was a background &ldquo;please wait&rdquo; speaker; created feedback loops."],
        ["voice_clone.py", "Legacy ElevenLabs wrapper, now a no-op shim."],
        ["user_cache.py", "JSON disk cache for username + face id."],
        ["reset_identity.py", "Standalone script: ssh + run to wipe face DB."],
    ]),
    H2("server/ (Python 3.11+, lives on the Mac)"),
    kv_table([
        ["server.py", "Flask app. /health, /turn, /stream_turn, /tts, /greet."],
        ["config.py", "Typed env loader."],
        ["safety.py", "crisis_check() with 988 hotline reply."],
        ["session.py", "SQLiteSession wrapper + camera consent + recaps."],
        ["motion_trigger.py", "Pattern-based body-action shortcut."],
        ["openai_tts.py", "OpenAI tts-1 + ffmpeg amplifier."],
        ["deepgram_asr.py", "Deepgram client (currently dormant)."],
        ["semantic_endpoint.py", "Experimental LLM-based endpointing (off)."],
        ["streaming.py", "SSE helpers for /stream_turn."],
        ["memory.py / memory_rollup.py", "Cross-session summary memory."],
        ["agents/router.py", "Triage agent."],
        ["agents/chat.py", "General convo + 18 NAO action tools."],
        ["agents/chatbot.py", "Pinecone RAG agent."],
        ["agents/skills.py", "Time, weather, timers, todos."],
        ["agents/therapist.py", "Empathic listener + body-action tools."],
        ["agents/cbt_coach.py", "Thought-record walker."],
        ["agents/grounding_coach.py", "5-4-3-2-1 / box breathing / body scan."],
        ["agents/mi_coach.py", "Experimental MI agent."],
        ["tools/nao_actions.py", "18 @function_tool entries that enqueue actions."],
        ["tools/pinecone_search.py", "Single RAG tool."],
        ["tools/emotion.py", "observe_face, log_emotion, distortion, reframe, consent, recap."],
        ["tools/skills_tools.py", "Time/timer/todo helpers."],
    ]),
    PageBreak(),
]

# 16. TROUBLESHOOTING
story += [
    H1("16. Troubleshooting &amp; gotchas"),
    H2("&ldquo;The robot is silent.&rdquo;"),
    B("Is <code>main.py</code> running? <code>ssh nao@... 'pgrep -af main.py'</code>."),
    B("Did the wake listener arm? Tail nao.log for &ldquo;[wake] armed&rdquo;."),
    B("Volume reset by some service? <code>main.py</code> pins it to 100 at boot, but rebooting from the chest button resets it. Re-run <code>./run.sh</code>."),
    H2("&ldquo;The robot keeps re-asking my name.&rdquo;"),
    B("<code>_USER_CACHE</code> got cleared by a crash. Check that <code>~/nao_assist/user_cache.json</code> exists on the robot."),
    B("Face was learned under a previous name. <code>python nao/reset_identity.py</code> wipes the DB."),
    H2("&ldquo;Robot hears itself.&rdquo;"),
    B("Self-echo guard threshold too low. Bump bigram-overlap rejection from 0.6 &rarr; 0.5 in <code>_is_self_echo</code>."),
    B("Or raise <code>silent_th</code> in <code>audio_handler.py</code>."),
    H2("&ldquo;Latency feels bad.&rdquo>"),
    B("Confirm <code>USE_DEEPGRAM=0</code> &mdash; Deepgram on this network adds 1-2 s."),
    B("Check that motion commands hit the trigger: tail server.log for &ldquo;[motion-trigger]&rdquo;."),
    B("If the LLM round-trip is slow, set <code>OPENAI_BASE_URL</code> to a region close to you."),
    H2("&ldquo;Two voices at once.&rdquo;"),
    B("Old <code>processing_announcer</code> sneaking back in. It is disabled but if you re-enable it, mute <code>ALTextToSpeech</code> too."),
    B("<code>got_audio</code> flag in <code>stream_tts.py</code> must be set on the first audio event."),
    H2("&ldquo;Stale code on the robot.&rdquo;"),
    B("Always <code>./run.sh</code>, never manual rsync without the <code>--exclude '*.pyc'</code> flag."),
    PageBreak(),
]

# 17. GLOSSARY
story += [
    H1("17. Glossary"),
    kv_table([
        ["NAOqi", "Aldebaran/SoftBank&rsquo;s middleware. Python 2.7 + C++. Provides ALMotion, ALAudioDevice, etc."],
        ["VAD", "Voice activity detection. We use simple energy-based + webrtcvad as cross-check."],
        ["ASR", "Automatic speech recognition. Whisper (gpt-4o-mini-transcribe) here."],
        ["TTS", "Text-to-speech. OpenAI tts-1 with the &lsquo;nova&rsquo; voice."],
        ["SSE", "Server-Sent Events. Long-lived HTTP response streaming JSON-line events."],
        ["Agents SDK", "openai-agents Python lib: handoffs + function tools + sessions."],
        ["Handoff", "An agent transferring control to another agent mid-run."],
        ["function_tool", "Decorator that exposes a Python function to the LLM as a callable tool."],
        ["RAG", "Retrieval-augmented generation. Embed query &rarr; vector DB &rarr; inject hits into prompt."],
        ["Pinecone", "The vector database we use for the Morgan CS index."],
        ["mDNS", "Multicast DNS &mdash; the &lsquo;nao.local&rsquo; hostname resolution."],
        ["DHCP reservation", "What you ask Morgan IT for when you want a static-ish IP."],
        ["actions_queue", "Per-run list on the agent context that NAO action tools append to."],
        ["motion_trigger", "Our regex shortcut for clear body-action phrases &mdash; bypasses the LLM."],
        ["crisis gate", "Hardcoded 988 reply that runs before any agent sees the user message."],
    ]),
    Spacer(1, 0.3 * inch),
    H2("End of walkthrough"),
    P(
        "If you load this PDF into NotebookLM and ask it &ldquo;walk me through the voice pipeline&rdquo; "
        "or &ldquo;how does the therapist use the camera?&rdquo; you should get answers grounded in the "
        "actual architecture above. For deeper code questions, point NotebookLM at the repo as a "
        "second source &mdash; this PDF + the code together cover everything."
    ),
]


def main():
    doc = SimpleDocTemplate(
        OUT, pagesize=LETTER,
        leftMargin=0.85 * inch, rightMargin=0.85 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title="Nao-OpenAI-Morgan-Assist Walkthrough",
        author="Aayush Shrestha",
    )

    def _on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(GRAY)
        if doc.page > 1:
            canvas.drawString(0.85 * inch, 0.4 * inch, "Nao-OpenAI-Morgan-Assist  ·  walkthrough")
            canvas.drawRightString(LETTER[0] - 0.85 * inch, 0.4 * inch, f"page {doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
