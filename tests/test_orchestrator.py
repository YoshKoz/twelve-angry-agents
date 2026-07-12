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
