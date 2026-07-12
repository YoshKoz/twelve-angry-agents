import json

import pytest

from orchestrator import Deliberation

CARDS = [
    {"id": f"juror_{n:02d}", "seat": n, "name": f"J{n}", "occupation": f"job{n}",
     "temperament": "t", "biases": "b", "speech_style": "s"}
    for n in range(1, 13)
]


def collect():
    events = []
    return events, events.append


def make_juror(speech="I think...", lean="undecided", vote="guilty"):
    def fn(card, case_text, transcript, mode):
        if mode == "speak":
            return {"speech": speech, "lean": lean, "confidence": 0.5}
        return {"vote": vote}
    return fn


def scripted_foreman(actions):
    it = iter(actions)
    def fn(transcript, last_tally, turn, turn_cap):
        return next(it)
    return fn


def make_delib(juror_fn, foreman_fn, events_emit, tmp_path, cap=200):
    return Deliberation("CASE", CARDS, juror_fn, foreman_fn, events_emit,
                        turn_cap=cap, transcript_dir=tmp_path, run_id="test")


def test_call_on_records_speech_and_private_lean(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(speech="hello", lean="not_guilty"),
                   scripted_foreman([]), emit, tmp_path)
    d._call_on(5)
    assert [e["type"] for e in events] == ["speaker", "speech"]
    assert events[1] == {"type": "speech", "seat": 5, "name": "J5",
                         "speech": "hello"}
    assert d.leans[5] == {"lean": "not_guilty", "confidence": 0.5}
    assert d.transcript == [{"seat": 5, "name": "J5", "speech": "hello"}]
    assert d.spoke_since_vote is True


def test_call_on_juror_failure_becomes_pass(tmp_path):
    def broken(card, case_text, transcript, mode):
        raise RuntimeError("llm died")
    events, emit = collect()
    d = make_delib(broken, scripted_foreman([]), emit, tmp_path)
    d._call_on(2)
    assert events[-1]["type"] == "speech"
    assert "passes" in events[-1]["speech"]
    assert d.transcript == []          # a pass is not part of the record


def test_call_vote_polls_all_12_and_tallies(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(vote="guilty"), scripted_foreman([]), emit,
                   tmp_path)
    d._call_vote()
    types = [e["type"] for e in events]
    assert types[:2] == ["vote_called", "vote_result"]
    result = events[1]
    assert len(result["votes"]) == 12
    assert result["tally"]["guilty"] == 12
    # unanimous vote declares immediately
    assert events[2]["type"] == "verdict"
    assert d.verdict == "guilty"
    assert d.spoke_since_vote is False


def test_call_vote_invalid_or_failing_vote_counts_undecided(tmp_path):
    def flaky(card, case_text, transcript, mode):
        if card["seat"] == 1:
            raise RuntimeError("dead")
        if card["seat"] == 2:
            return {"vote": "banana"}
        return {"vote": "guilty"}
    events, emit = collect()
    d = make_delib(flaky, scripted_foreman([]), emit, tmp_path)
    d._call_vote()
    tally = events[1]["tally"]
    assert tally == {"guilty": 10, "not_guilty": 0, "undecided": 2}
    assert d.verdict is None


def test_private_leans_never_emitted(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(lean="guilty"), scripted_foreman([]), emit,
                   tmp_path)
    d._call_on(1)
    d._call_vote()
    blob = json.dumps(events)
    assert "lean" not in blob
    assert "confidence" not in blob


def test_write_transcript_dumps_all_events(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(), scripted_foreman([]), emit, tmp_path)
    d._call_on(1)
    path = d._write_transcript()
    saved = json.loads(path.read_text())
    assert saved == events
    assert path.name == "test.json"


def split_juror(guilty_seats):
    """Jurors in guilty_seats vote guilty, the rest not_guilty."""
    def fn(card, case_text, transcript, mode):
        if mode == "speak":
            return {"speech": f"J{card['seat']} speaks.",
                    "lean": "undecided", "confidence": 0.5}
        v = "guilty" if card["seat"] in guilty_seats else "not_guilty"
        return {"vote": v}
    return fn


def test_run_unanimous_on_opening_vote(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(vote="not_guilty"), scripted_foreman([]),
                   emit, tmp_path)
    assert d.run() == "not_guilty"
    types = [e["type"] for e in events]
    assert types == ["case", "roster", "vote_called", "vote_result",
                     "verdict"]
    assert (tmp_path / "test.json").exists()


def test_run_foreman_drives_discussion_then_declares_hung(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([
        {"action": "call_on", "target": 8},
        {"action": "call_on", "target": 3},
        {"action": "declare", "verdict": "hung", "reason": "deadlock"},
    ])
    d = make_delib(split_juror({1, 2, 3, 4, 5, 6}), foreman, emit, tmp_path)
    assert d.run() == "hung"
    speeches = [e for e in events if e["type"] == "speech"]
    assert [s["seat"] for s in speeches] == [8, 3]
    assert events[-1] == {"type": "verdict", "verdict": "hung",
                          "reason": "deadlock"}


def test_run_turn_cap_forces_hung(tmp_path):
    events, emit = collect()
    def always_call_on(transcript, last_tally, turn, turn_cap):
        return {"action": "call_on", "target": 8}
    d = make_delib(split_juror({1}), always_call_on, emit, tmp_path, cap=5)
    assert d.run() == "hung"
    assert events[-1]["reason"] == "turn cap reached"
    speeches = [e for e in events if e["type"] == "speech"]
    assert len(speeches) == 5


def test_no_back_to_back_votes(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([
        {"action": "call_vote"},                  # right after opening vote
        {"action": "declare", "verdict": "hung", "reason": "x"},
    ])
    d = make_delib(split_juror({1, 2, 3}), foreman, emit, tmp_path)
    d.run()
    types = [e["type"] for e in events]
    # only ONE vote_called (the forced opener); second call_vote was
    # converted to a round-robin call_on
    assert types.count("vote_called") == 1
    assert "speech" in types


def test_premature_declare_rejected(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([
        {"action": "declare", "verdict": "guilty", "reason": "im tired"},
        {"action": "declare", "verdict": "hung", "reason": "ok fine"},
    ])
    d = make_delib(split_juror({1, 2, 3}), foreman, emit, tmp_path)
    assert d.run() == "hung"       # guilty declare bounced (tally not 12-0)
    speeches = [e for e in events if e["type"] == "speech"]
    assert len(speeches) == 1      # bounce became a round-robin call_on


def test_malformed_foreman_falls_back_to_round_robin(tmp_path):
    events, emit = collect()
    calls = {"n": 0}
    def flaky_foreman(transcript, last_tally, turn, turn_cap):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("malformed")
        return {"action": "declare", "verdict": "hung", "reason": "x"}
    d = make_delib(split_juror({1, 2}), flaky_foreman, emit, tmp_path)
    assert d.run() == "hung"
    speeches = [e for e in events if e["type"] == "speech"]
    assert [s["seat"] for s in speeches] == [1]   # round-robin starts at 1
