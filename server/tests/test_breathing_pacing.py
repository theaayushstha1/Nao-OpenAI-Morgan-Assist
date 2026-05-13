from server.breathing_pacing import expand_tts_pacing


def test_ssml_break_tags_become_robot_pauses():
    chunks = expand_tts_pacing(
        'Breathe in: one<break time="800ms"/>two'
        '<break time="800ms"/>three<break time="800ms"/>four.'
    )

    assert chunks == [
        ("Breathe in: one", 800),
        ("two", 800),
        ("three", 800),
        ("four.", 0),
    ]


def test_bare_count_sentence_is_paced():
    chunks = expand_tts_pacing("1 2 3 4.")

    assert chunks == [
        ("one", 800),
        ("two", 800),
        ("three", 800),
        ("four", 800),
    ]


def test_inline_breath_count_is_paced():
    chunks = expand_tts_pacing("Breathe out: 1 2 3 4 5 6.")

    assert chunks == [
        ("Breathe out: one", 800),
        ("two", 800),
        ("three", 800),
        ("four", 800),
        ("five", 800),
        ("six", 800),
    ]


def test_normal_numbers_are_not_split():
    chunks = expand_tts_pacing("I have 1 2 reasons, but not a breathing count.")

    assert chunks == [("I have 1 2 reasons, but not a breathing count.", 0)]
