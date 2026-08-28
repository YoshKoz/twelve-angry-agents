"""live_agents wires the orchestrator's injected-callable signatures to real
LLM calls. Every agent.ask_json_detailed call is patched out, so nothing here
touches a model."""

import inspect
from unittest.mock import MagicMock, patch

import pytest

import live_agents
import orchestrator

CARD = {"id": "juror_08", "seat": 8, "name": "James Davis",
        "occupation": "architect", "temperament": "t", "biases": "b",
        "speech_style": "s", "emoji": "\U0001F914"}

NARRATIVE = "THE FULL NARRATIVE RECORD, every witness account in full."

EXHIBIT = {"id": "the_knife", "name": "The switchblade",
           "prosecution_claim": "It is one of a kind.",
           "record": "The handle was carved."}

CASE = {"id": "the_stabbing", "title": "The State v. the Defendant",
        "charge": "First-degree murder.", "narrative": NARRATIVE,
        "exhibits": [EXHIBIT]}

FINDINGS = [{"name": "The switchblade", "summary": "8 doubting it"}]

TRANSCRIPT = [{"seat": 3, "name": "Frank", "speech": "He's guilty."}]


def _mock_detailed(parsed):
    return MagicMock(return_value=(dict(parsed), "raw out", "sys", "user"))


@pytest.fixture(autouse=True)
def _no_tts():
    """TTS is a network call; the orchestrator only cares that audio is
    optional, so it is stubbed for every test here."""
    with patch("live_agents.tts.generate", return_value="AUDIO") as m:
        yield m


def _args(mock):
    return mock.call_args[0]


def _kwargs(mock):
    return mock.call_args[1]


# --- assessing an exhibit --------------------------------------------------

def test_live_assess_wires_the_exhibit_and_the_position_schema():
    mock = _mock_detailed({"position": "raises_doubt", "reasoning": "r",
                           "confidence": 0.4})
    with patch("live_agents.agent.ask_json_detailed", mock):
        out = live_agents.live_assess_fn(CARD, CASE, EXHIBIT, FINDINGS)
    assert out["position"] == "raises_doubt"
    assert out["_prompt"] == {"system": "sys", "user": "user"}
    assert out["_raw_output"] == "raw out"
    system, user, keys = _args(mock)[:3]
    assert "Juror #8" in system
    assert "EXHIBIT BEFORE THE ROOM: The switchblade" in user
    assert "WHAT THIS ROOM HAS ALREADY SETTLED" in user
    assert keys == ["position", "reasoning"]


def test_live_assess_works_before_anything_is_settled():
    mock = _mock_detailed({"position": "inconclusive", "reasoning": "r"})
    with patch("live_agents.agent.ask_json_detailed", mock):
        live_agents.live_assess_fn(CARD, CASE, EXHIBIT)
    assert "ALREADY SETTLED" not in _args(mock)[1]


def test_assessments_get_a_tighter_budget_than_a_speaking_turn():
    """Twelve of them run at once and each is short; a stuck one should
    surface as a failure sooner than a full turn would."""
    mock = _mock_detailed({"position": "inconclusive", "reasoning": "r"})
    with patch("live_agents.agent.ask_json_detailed", mock):
        live_agents.live_assess_fn(CARD, CASE, EXHIBIT, [])
    assert _kwargs(mock)["timeout"] == live_agents.ASSESS_TIMEOUT
    assert live_agents.ASSESS_TIMEOUT < live_agents.TIMEOUT


def test_assessors_run_on_the_juror_model():
    mock = _mock_detailed({"position": "inconclusive", "reasoning": "r"})
    with patch("live_agents.agent.model_for", return_value="haiku") as m, \
         patch("live_agents.agent.ask_json_detailed", mock):
        live_agents.live_assess_fn(CARD, CASE, EXHIBIT, [])
    m.assert_called_once_with("juror")
    assert _kwargs(mock)["model"] == "haiku"


# --- jurors ----------------------------------------------------------------

def test_live_juror_speak_wires_prompts_and_schema():
    mock = _mock_detailed({"speech": "x", "lean": "guilty", "confidence": 0.9})
    with patch("live_agents.agent.ask_json_detailed", mock):
        out = live_agents.live_juror_fn(CARD, CASE, TRANSCRIPT, "speak")
    assert out["speech"] == "x"
    assert out["_prompt"] == {"system": "sys", "user": "user"}
    assert out["_raw_output"] == "raw out"
    system, user, keys = _args(mock)[:3]
    assert "Juror #8" in system
    assert CASE["charge"] in user
    assert "He's guilty." in user
    assert keys == ["speech", "lean"]


def test_live_juror_speak_attaches_audio(_no_tts):
    with patch("live_agents.agent.ask_json_detailed",
               _mock_detailed({"speech": "hello there", "lean": "guilty"})):
        out = live_agents.live_juror_fn(CARD, CASE, [], "speak")
    assert out["audio"] == "AUDIO"
    _no_tts.assert_called_once_with("hello there", 8)


def test_live_juror_speak_survives_a_tts_failure(_no_tts):
    _no_tts.side_effect = RuntimeError("elevenlabs down")
    with patch("live_agents.agent.ask_json_detailed",
               _mock_detailed({"speech": "x", "lean": "guilty"})):
        out = live_agents.live_juror_fn(CARD, CASE, [], "speak")
    assert "audio" not in out
    assert out["speech"] == "x"


def test_live_juror_speak_passes_the_tally_note_exhibit_and_findings():
    mock = _mock_detailed({"speech": "x", "lean": "guilty"})
    with patch("live_agents.agent.ask_json_detailed", mock):
        live_agents.live_juror_fn(
            CARD, CASE, [], "speak",
            {"guilty": 4, "not_guilty": 2, "undecided": 6},
            "Juror #3 has put a direct question to you.", None, EXHIBIT,
            FINDINGS)
    user = _args(mock)[1]
    assert "guilty 4, not guilty 2, undecided 6" in user
    assert "Juror #3 has put a direct question to you." in user
    assert "EXHIBIT BEFORE THE ROOM: The switchblade" in user
    assert "WHAT THIS ROOM HAS ALREADY SETTLED" in user


def test_live_juror_vote_requires_reasoning_then_vote():
    mock = _mock_detailed({"reasoning": "r", "vote": "not_guilty"})
    with patch("live_agents.agent.ask_json_detailed", mock):
        out = live_agents.live_juror_fn(CARD, CASE, [], "vote")
    assert out["vote"] == "not_guilty"
    assert _args(mock)[2] == ["reasoning", "vote"]
    assert out["_prompt"] is not None
    assert out["_raw_output"] == "raw out"
    assert "audio" not in out           # nobody speaks a ballot aloud


def test_live_juror_vote_defaults_to_a_show_of_hands():
    mock = _mock_detailed({"reasoning": "r", "vote": "guilty"})
    with patch("live_agents.agent.ask_json_detailed", mock):
        live_agents.live_juror_fn(CARD, CASE, [], "vote")
    assert "SHOW OF HANDS" in _args(mock)[1]


def test_live_juror_vote_honors_a_secret_ballot_and_carries_findings():
    mock = _mock_detailed({"reasoning": "r", "vote": "abstain"})
    with patch("live_agents.agent.ask_json_detailed", mock):
        live_agents.live_juror_fn(CARD, CASE, [], "vote",
                                  {"guilty": 1, "not_guilty": 1,
                                   "undecided": 10},
                                  None, "secret", EXHIBIT, FINDINGS)
    user = _args(mock)[1]
    assert "SECRET WRITTEN BALLOT" in user
    assert "guilty 1, not guilty 1, undecided 10" in user
    assert "WHAT THIS ROOM HAS ALREADY SETTLED" in user


def test_jurors_run_on_the_juror_model():
    mock = _mock_detailed({"speech": "x", "lean": "guilty"})
    with patch("live_agents.agent.model_for", return_value="haiku") as m, \
         patch("live_agents.agent.ask_json_detailed", mock):
        live_agents.live_juror_fn(CARD, CASE, [], "speak")
    m.assert_called_once_with("juror")
    assert _kwargs(mock)["model"] == "haiku"


# --- foreman ---------------------------------------------------------------

def test_live_foreman_requires_an_action():
    mock = _mock_detailed({"action": "call_vote"})
    with patch("live_agents.agent.ask_json_detailed", mock):
        out = live_agents.live_foreman_fn(CASE, [], None, 3, 200)
    assert out["action"] == "call_vote"
    assert out["_prompt"] is not None
    assert _args(mock)[2] == ["action"]
    assert "Turn 3 of at most 200" in _args(mock)[1]


def test_live_foreman_relays_pending_counts_and_the_judges_note():
    mock = _mock_detailed({"action": "call_on"})
    with patch("live_agents.agent.ask_json_detailed", mock):
        live_agents.live_foreman_fn(
            CASE, TRANSCRIPT,
            {"guilty": 6, "not_guilty": 6, "undecided": 0}, 9, 200,
            [{"seat": 5, "summary": "sends out for: the knife"}],
            {8: 4}, "Keep deliberating.")
    user = _args(mock)[1]
    assert "Juror #5: sends out for: the knife" in user
    assert "#8:4" in user
    assert "THE JUDGE SENT YOU BACK: Keep deliberating." in user
    assert "guilty 6, not guilty 6, undecided 0" in user


def test_live_foreman_is_offered_the_close_only_with_an_exhibit_open():
    mock = _mock_detailed({"action": "close_exhibit"})
    with patch("live_agents.agent.ask_json_detailed", mock):
        live_agents.live_foreman_fn(CASE, [], None, 9, 200, None, {8: 0},
                                    None, EXHIBIT, FINDINGS, 3, 2)
    user = _args(mock)[1]
    assert '"action": "close_exhibit"' in user
    assert "spent 3 turns on this exhibit" in user
    assert "2 exhibits remain on the docket" in user
    assert "WHAT THIS ROOM HAS ALREADY SETTLED" in user
    with patch("live_agents.agent.ask_json_detailed", mock):
        live_agents.live_foreman_fn(CASE, [], None, 9, 200)
    assert "close_exhibit" not in _args(mock)[1]
    assert "The docket is finished" in _args(mock)[1]


def test_the_foreman_runs_on_the_foreman_model():
    mock = _mock_detailed({"action": "call_vote"})
    with patch("live_agents.agent.model_for", return_value="sonnet") as m, \
         patch("live_agents.agent.ask_json_detailed", mock):
        live_agents.live_foreman_fn(CASE, [], None, 1, 200)
    m.assert_called_once_with("foreman")
    assert _kwargs(mock)["model"] == "sonnet"


# --- bailiff ---------------------------------------------------------------

def test_live_bailiff_answers_an_evidence_request_from_the_record():
    mock = _mock_detailed({"granted": True, "record": "It is a switchblade."})
    with patch("live_agents.agent.ask_json_detailed", mock):
        out = live_agents.live_bailiff_fn(
            "evidence", {"type": "request_evidence", "item": "the knife"}, 8,
            CASE, TRANSCRIPT)
    assert out["record"] == "It is a switchblade."
    system, user, keys = _args(mock)[:3]
    assert "court officer" in system
    assert "Juror #8 has sent out for: the knife" in user
    assert NARRATIVE in user
    assert keys == ["granted", "record"]


def test_live_bailiff_reports_an_experiment():
    mock = _mock_detailed({"possible": True, "result": "41 seconds."})
    with patch("live_agents.agent.ask_json_detailed", mock):
        out = live_agents.live_bailiff_fn(
            "experiment",
            {"type": "propose_experiment", "description": "time the walk"},
            5, CASE, TRANSCRIPT)
    assert out["result"] == "41 seconds."
    _, user, keys = _args(mock)[:3]
    assert "time the walk" in user
    assert NARRATIVE in user
    assert "He's guilty." in user        # the room's discussion is context
    assert keys == ["possible", "result"]


def test_the_court_officer_is_the_only_agent_handed_the_whole_record():
    """Everyone else works from the brief and one exhibit; this is what keeps
    a run affordable and what keeps the jurors reasoning from the room."""
    calls = []

    def record_prompt(_system, user, _keys, **_kw):
        calls.append(user)
        return {"ok": 1}, "raw", "sys", user
    with patch("live_agents.agent.ask_json_detailed", record_prompt):
        live_agents.live_assess_fn(CARD, CASE, EXHIBIT, FINDINGS)
        live_agents.live_juror_fn(CARD, CASE, TRANSCRIPT, "speak", None, None,
                                  None, EXHIBIT, FINDINGS)
        live_agents.live_juror_fn(CARD, CASE, TRANSCRIPT, "vote", None, None,
                                  "secret", EXHIBIT, FINDINGS)
        live_agents.live_foreman_fn(CASE, TRANSCRIPT, None, 1, 200, None,
                                    {8: 0}, None, EXHIBIT, FINDINGS, 0, 0)
    assert calls and all(NARRATIVE not in user for user in calls)
    calls.clear()
    with patch("live_agents.agent.ask_json_detailed", record_prompt):
        live_agents.live_bailiff_fn("evidence", {"item": "x"}, 1, CASE, [])
        live_agents.live_bailiff_fn("experiment", {"description": "x"}, 1,
                                    CASE, [])
    assert len(calls) == 2 and all(NARRATIVE in user for user in calls)


def test_live_bailiff_tolerates_a_request_missing_its_field():
    mock = _mock_detailed({"granted": False, "record": "The record is silent."})
    with patch("live_agents.agent.ask_json_detailed", mock):
        live_agents.live_bailiff_fn("evidence", {}, 8, CASE, [])
    assert "sent out for:" in _args(mock)[1]


def test_the_bailiff_runs_on_the_structural_model():
    mock = _mock_detailed({"granted": True, "record": "x"})
    with patch("live_agents.agent.model_for", return_value="sonnet") as m, \
         patch("live_agents.agent.ask_json_detailed", mock):
        live_agents.live_bailiff_fn("evidence", {"item": "x"}, 1, CASE, [])
    m.assert_called_once_with("bailiff")
    assert _kwargs(mock)["model"] == "sonnet"


# --- judge -----------------------------------------------------------------

def test_live_judge_rules_on_the_verdict():
    mock = _mock_detailed({"accept": False, "instruction": "Go back."})
    with patch("live_agents.agent.ask_json_detailed", mock):
        out = live_agents.live_judge_fn(
            "guilty", "we all agree",
            {"guilty": 11, "not_guilty": 1, "undecided": 0}, 44, TRANSCRIPT)
    assert out["accept"] is False
    assert out["_raw_output"] == "raw out"
    system, user, keys = _args(mock)[:3]
    assert "trial judge" in system
    assert "Verdict announced: guilty" in user
    assert "guilty 11, not guilty 1, undecided 0" in user
    assert "deliberated 44 turns" in user
    assert keys == ["accept"]


def test_the_judge_runs_on_the_structural_model():
    mock = _mock_detailed({"accept": True})
    with patch("live_agents.agent.model_for", return_value="sonnet") as m, \
         patch("live_agents.agent.ask_json_detailed", mock):
        live_agents.live_judge_fn("hung", "x", None, 3, [])
    m.assert_called_once_with("judge")
    assert _kwargs(mock)["model"] == "sonnet"


# --- shared wiring ---------------------------------------------------------

def test_every_role_is_bounded_by_the_module_timeout_and_retries():
    mock = _mock_detailed({"action": "call_vote"})
    with patch("live_agents.agent.ask_json_detailed", mock):
        live_agents.live_foreman_fn(CASE, [], None, 1, 200)
    assert _kwargs(mock)["timeout"] == live_agents.TIMEOUT
    assert _kwargs(mock)["retries"] == live_agents.RETRIES
    assert live_agents.RETRIES < 3      # tighter than agent.py's own default


def test_every_role_names_a_model_rather_than_taking_the_default():
    mock = _mock_detailed({"x": 1})
    with patch("live_agents.agent.model_for", return_value="a-model"), \
         patch("live_agents.agent.ask_json_detailed", mock):
        for call in (
                lambda: live_agents.live_assess_fn(CARD, CASE, EXHIBIT, []),
                lambda: live_agents.live_juror_fn(CARD, CASE, [], "speak"),
                lambda: live_agents.live_juror_fn(CARD, CASE, [], "vote"),
                lambda: live_agents.live_foreman_fn(CASE, [], None, 1, 200),
                lambda: live_agents.live_bailiff_fn("evidence", {}, 1, CASE,
                                                    []),
                lambda: live_agents.live_judge_fn("hung", "x", None, 1, [])):
            call()
            assert _kwargs(mock)["model"] == "a-model"


def test_the_live_adapters_match_the_signatures_the_orchestrator_calls():
    """These are positional calls made from orchestrator.py; a rename or a
    dropped parameter here is a live-only crash the fakes cannot catch."""
    def params(fn):
        return list(inspect.signature(fn).parameters)

    assert params(live_agents.live_assess_fn) == [
        "card", "case", "exhibit", "findings"]
    assert params(live_agents.live_juror_fn) == [
        "card", "case", "transcript", "mode", "last_tally", "floor_note",
        "vote_method", "exhibit", "findings"]
    assert params(live_agents.live_foreman_fn) == [
        "case", "transcript", "last_tally", "turn", "turn_cap", "pending",
        "speech_counts", "judge_note", "exhibit", "findings", "exhibit_turns",
        "remaining"]
    assert params(live_agents.live_bailiff_fn) == [
        "kind", "request", "seat", "case", "transcript"]
    assert params(live_agents.live_judge_fn) == [
        "verdict", "reason", "last_tally", "turn", "transcript"]


def test_a_live_room_runs_end_to_end_without_reaching_a_backend(tmp_path):
    """The adapters and the orchestrator agree on how they call each other."""
    replies = {
        "position": {"position": "raises_doubt", "reasoning": "r"},
        "speech": {"speech": "I have a doubt.", "lean": "not_guilty",
                   "confidence": 0.6, "action": {"type": "none"}},
        "vote": {"reasoning": "r", "vote": "not_guilty"},
        "accept": {"accept": True, "instruction": "So say you all."},
    }
    foreman_actions = iter([
        {"action": "call_on", "target": 1},
        {"action": "close_exhibit", "finding": "it does not hold up"},
        {"action": "call_vote", "method": "hands", "binding": True},
        {"action": "declare", "verdict": "not_guilty", "reason": "no doubt"},
    ])

    def fake(_system, user, keys, **_kw):
        if keys == ["action"]:
            return dict(next(foreman_actions)), "raw", "sys", user
        return dict(replies[keys[0]]), "raw", "sys", user

    cards = [{"id": f"j{n}", "seat": n, "name": f"J{n}", "occupation": "o",
              "temperament": "t", "biases": "b", "speech_style": "s"}
             for n in range(1, 13)]
    events = []
    with patch("live_agents.agent.ask_json_detailed", fake), \
         patch("live_agents.agent.model_for", return_value=None):
        d = orchestrator.Deliberation(
            CASE, cards, live_agents.live_juror_fn,
            live_agents.live_foreman_fn, events.append,
            transcript_dir=tmp_path, run_id="live",
            bailiff_fn=live_agents.live_bailiff_fn,
            judge_fn=live_agents.live_judge_fn,
            assess_fn=live_agents.live_assess_fn)
        assert d.run() == "not_guilty"
    kinds = [e["type"] for e in events]
    assert kinds.count("assessed") == 12
    assert "positions" in kinds and "exhibit_closed" in kinds
    assert "docket_closed" in kinds
    assert "error" not in kinds
