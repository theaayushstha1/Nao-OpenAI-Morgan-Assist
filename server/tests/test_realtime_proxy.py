from server import config, realtime_proxy


def test_realtime_session_auto_responds_and_transcribes():
    session = realtime_proxy.DEFAULT_SESSION["session"]
    turn_detection = session["turn_detection"]

    assert session["input_audio_format"] == "pcm16"
    assert session["output_audio_format"] == "pcm16"
    assert session["input_audio_noise_reduction"] == {"type": "far_field"}
    assert session["input_audio_transcription"]["model"] == config.WHISPER_MODEL
    assert turn_detection["type"] == "server_vad"
    assert turn_detection["threshold"] == config.REALTIME_VAD_THRESHOLD
    assert turn_detection["create_response"] is False
    assert turn_detection["interrupt_response"] is True


def test_realtime_url_uses_config_model():
    assert "model={0}".format(config.REALTIME_MODEL) in realtime_proxy.REALTIME_URL


def test_text_frame_decodes_bytes_for_openai_text_opcode():
    assert realtime_proxy._text_frame(b'{"type":"ping"}') == '{"type":"ping"}'


def test_text_frame_wraps_binary_pcm_as_audio_append():
    frame = realtime_proxy._text_frame(b"\xfe\x00\x01\x02")
    event = __import__("json").loads(frame)

    assert event["type"] == "input_audio_buffer.append"
    assert event["audio"]


def test_realtime_transcripts_are_logged(capsys):
    realtime_proxy._log_upstream_event(
        "alice",
        '{"type":"conversation.item.input_audio_transcription.completed",'
        '"transcript":"tell me about AI"}',
    )

    out = capsys.readouterr().out
    assert "[realtime transcript]" in out
    assert "tell me about AI" in out


def test_client_session_update_is_forced_to_manual_response_create():
    text = realtime_proxy._sanitize_client_event(
        '{"type":"session.update","session":{"turn_detection":'
        '{"type":"server_vad","create_response":true}}}'
    )
    event = __import__("json").loads(text)

    assert event["session"]["turn_detection"]["create_response"] is False
    assert event["session"]["turn_detection"]["interrupt_response"] is True


def test_proxy_drops_client_response_create_duplicates():
    assert realtime_proxy._sanitize_client_event('{"type":"response.create"}') is None


def test_junk_realtime_transcript_filter():
    assert realtime_proxy._looks_like_junk_transcript("")
    assert realtime_proxy._looks_like_junk_transcript("world right now")
    assert not realtime_proxy._looks_like_junk_transcript("tell me about AI")
