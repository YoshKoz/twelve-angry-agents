import prompts

CARD = {
    "id": "juror_03", "seat": 3, "name": "Frank Della Rocca",
    "occupation": "messenger-service owner",
    "temperament": "loud, quick to anger",
    "biases": "takes disagreement personally",
    "speech_style": "blunt, jabbing",
}

TRANSCRIPT = [
    {"seat": 8, "name": "Davis", "speech": "I just want to talk."},
    {"seat": 3, "name": "Frank Della Rocca", "speech": "Talk about what?"},
]


def test_honesty_rule_in_every_system_prompt():
    assert prompts.HONESTY_RULE in prompts.juror_system_prompt(CARD)
    assert prompts.HONESTY_RULE in prompts.foreman_system_prompt()


def test_juror_system_prompt_has_card_fields_but_no_script_hints():
    sys = prompts.juror_system_prompt(CARD)
    for field in ("seat", "name", "occupation", "temperament",
                  "biases", "speech_style"):
        assert str(CARD[field]) in sys or field == "seat"
    assert "Juror #3" in sys
    # blind flip: no dissent/plot hints (honesty rule's own wording exempt)
    body = prompts.juror_system_prompt(CARD).replace(prompts.HONESTY_RULE, "")
    for word in ("dissent", "holdout", "fonda", "12 angry"):
        assert word.lower() not in body.lower()


def test_format_transcript_lines_and_empty():
    text = prompts.format_transcript(TRANSCRIPT)
    assert "Juror #8 (Davis): I just want to talk." in text
    assert prompts.format_transcript([]) == "(no one has spoken yet)"


def test_juror_speak_prompt_contains_case_transcript_and_schema():
    p = prompts.juror_speak_prompt("THE CASE", TRANSCRIPT)
    assert "THE CASE" in p
    assert "Talk about what?" in p
    for key in ('"speech"', '"lean"', '"confidence"'):
        assert key in p


def test_juror_vote_prompt_asks_only_for_vote():
    p = prompts.juror_vote_prompt("THE CASE", TRANSCRIPT)
    assert '"vote"' in p
    assert '"confidence"' not in p


def test_foreman_prompt_lists_all_three_actions():
    p = prompts.foreman_prompt(TRANSCRIPT, {"guilty": 7, "not_guilty": 5,
                                            "undecided": 0}, 12, 200)
    for action in ("call_on", "call_vote", "declare"):
        assert action in p
    assert "Turn 12 of max 200" in p
