from unittest.mock import patch, MagicMock

import live_agents

CARD = {"id": "juror_08", "seat": 8, "name": "James Davis",
        "occupation": "architect", "temperament": "t", "biases": "b",
        "speech_style": "s", "emoji": "\U0001F914"}


def _mock_detailed(parsed):
    return MagicMock(return_value=(parsed, 'raw out', 'sys', 'user'))


def test_live_juror_speak_wires_prompts_and_schema():
    mock = _mock_detailed({"speech": "x", "lean": "guilty", "confidence": 0.9})
    with patch("live_agents.agent.ask_json_detailed", mock):
        out = live_agents.live_juror_fn(CARD, "CASE", [], "speak")
    assert out["speech"] == "x"
    assert out["_prompt"] == {"system": "sys", "user": "user"}
    assert out["_raw_output"] == "raw out"
    sys_prompt, user_prompt = mock.call_args[0][0], mock.call_args[0][1]
    assert "Juror #8" in sys_prompt
    assert "CASE" in user_prompt
    assert mock.call_args[0][2] == ["speech", "lean", "confidence"]


def test_live_juror_vote_requires_reasoning_then_vote():
    with patch("live_agents.agent.ask_json_detailed",
               _mock_detailed({"reasoning": "r", "vote": "not_guilty"})) as mock:
        out = live_agents.live_juror_fn(CARD, "CASE", [], "vote")
    assert out["vote"] == "not_guilty"
    assert out["reasoning"] == "r"
    # think-then-vote: reasoning is required alongside the vote
    assert mock.call_args[0][2] == ["reasoning", "vote"]
    assert out["_prompt"] is not None
    assert out["_raw_output"] == "raw out"


def test_live_foreman_requires_action_key():
    mock = _mock_detailed({"action": "call_vote"})
    with patch("live_agents.agent.ask_json_detailed", mock):
        out = live_agents.live_foreman_fn([], None, 3, 200)
    assert out["action"] == "call_vote"
    assert out["_prompt"] is not None
    assert "Turn 3 of max 200" in mock.call_args[0][1]


def test_live_juror_speak_passes_last_tally_to_prompt():
    mock = _mock_detailed({"speech": "x", "lean": "guilty", "confidence": 0.9})
    with patch("live_agents.agent.ask_json_detailed", mock):
        live_agents.live_juror_fn(CARD, "CASE", [], "speak",
                                  {"guilty": 4, "not_guilty": 2, "undecided": 6})
    user_prompt = mock.call_args[0][1]
    assert "guilty 4, not guilty 2, undecided 6" in user_prompt


def test_live_juror_vote_passes_last_tally_to_prompt():
    mock = _mock_detailed({"vote": "guilty"})
    with patch("live_agents.agent.ask_json_detailed", mock):
        live_agents.live_juror_fn(CARD, "CASE", [], "vote",
                                  {"guilty": 1, "not_guilty": 1, "undecided": 10})
    user_prompt = mock.call_args[0][1]
    assert "guilty 1, not guilty 1, undecided 10" in user_prompt
