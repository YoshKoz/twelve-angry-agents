import json

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
    def fn(_card, _case_text, _transcript, mode, _last_tally=None):
        if mode == "speak":
            return {"speech": speech, "lean": lean, "confidence": 0.5}
        return {"vote": vote}
    return fn


def scripted_foreman(actions):
    it = iter(actions)
    def fn(_transcript, _last_tally, _turn, _turn_cap):
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
                         "speech": "hello", "reconsidering": False}
    assert d.leans[5] == {"lean": "not_guilty", "confidence": 0.5}
    assert d.transcript == [{"seat": 5, "name": "J5", "speech": "hello"}]
    assert d.spoke_since_vote is True


def test_call_on_juror_failure_becomes_pass(tmp_path):
    def broken(_card, _case_text, _transcript, _mode, _last_tally=None):
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
    assert types[0] == "vote_called"
    assert types.count("voter_done") == 12   # one progress ping per seat
    result = next(e for e in events if e["type"] == "vote_result")
    assert len(result["votes"]) == 12
    assert result["tally"]["guilty"] == 12
    # unanimous vote declares immediately after the tally
    assert types[-1] == "verdict"
    assert d.verdict == "guilty"
    assert d.spoke_since_vote is False


def test_call_vote_invalid_or_failing_vote_counts_undecided(tmp_path):
    def flaky(card, case_text, transcript, mode, _last_tally=None):
        if card["seat"] == 1:
            raise RuntimeError("dead")
        if card["seat"] == 2:
            return {"vote": "banana"}
        return {"vote": "guilty"}
    events, emit = collect()
    d = make_delib(flaky, scripted_foreman([]), emit, tmp_path)
    d._call_vote()
    result = next(e for e in events if e["type"] == "vote_result")
    assert result["tally"] == {"guilty": 10, "not_guilty": 0, "undecided": 2}
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
    def fn(card, _case_text, _transcript, mode, _last_tally=None):
        if mode == "speak":
            return {"speech": f"J{card['seat']} speaks.",
                    "lean": "undecided", "confidence": 0.5}
        v = "guilty" if card["seat"] in guilty_seats else "not_guilty"
        return {"vote": v}
    return fn


def test_opening_ballot_is_a_nonbinding_straw_poll(tmp_path):
    # even a unanimous opening show of hands must not end the case — the room
    # deliberates first. The foreman then declares hung to close the test.
    events, emit = collect()
    foreman = scripted_foreman([{"action": "declare", "verdict": "hung",
                                 "reason": "no talk needed"}])
    d = make_delib(make_juror(vote="not_guilty"), foreman, emit, tmp_path)
    assert d.run() == "hung"
    types = [e["type"] for e in events]
    assert types[:3] == ["case", "roster", "vote_called"]
    opening = next(e for e in events if e["type"] == "vote_result")
    assert opening["binding"] is False
    assert opening["tally"]["not_guilty"] == 12   # unanimous...
    # ...yet the verdict came from the foreman's declare, NOT the straw poll:
    # a straw-poll unanimity would have read "unanimous vote".
    verdict = events[-1]
    assert verdict["type"] == "verdict"
    assert verdict["reason"] == "no talk needed"
    assert (tmp_path / "test.json").exists()


def test_binding_unanimous_vote_declares(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(vote="not_guilty"), scripted_foreman([]),
                   emit, tmp_path)
    d._call_vote(binding=True)          # a real ballot, not the straw poll
    assert d.verdict == "not_guilty"
    assert events[-1] == {"type": "verdict", "verdict": "not_guilty",
                          "reason": "unanimous vote"}


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
    def always_call_on(_transcript, _last_tally, _turn, _turn_cap):
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
    def flaky_foreman(_transcript, _last_tally, _turn, _turn_cap):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("malformed")
        return {"action": "declare", "verdict": "hung", "reason": "x"}
    d = make_delib(split_juror({1, 2}), flaky_foreman, emit, tmp_path)
    assert d.run() == "hung"
    speeches = [e for e in events if e["type"] == "speech"]
    assert [s["seat"] for s in speeches] == [1]   # round-robin starts at 1


def test_dead_backend_aborts_instead_of_grinding(tmp_path):
    def dead(_card, _case_text, _transcript, _mode, _last_tally=None):
        raise ConnectionError("refused")
    def dead_foreman(_transcript, _last_tally, _turn, _turn_cap):
        raise ConnectionError("refused")
    events, emit = collect()
    d = make_delib(dead, dead_foreman, emit, tmp_path)
    verdict = d.run()
    assert verdict == "aborted"
    assert d.turn < 10                     # no 200-turn pass grind
    assert events[-1]["type"] == "error"
    assert "consecutive" in events[-1]["message"]


def test_failure_counter_resets_on_success(tmp_path):
    calls = {"n": 0}
    def flaky(_card, _case_text, _transcript, mode, _last_tally=None):
        if mode == "vote":
            return {"vote": "undecided"}
        calls["n"] += 1
        if calls["n"] % 2:                 # alternate fail/succeed
            raise ConnectionError("refused")
        return {"speech": "ok", "lean": "undecided", "confidence": 0.5}
    events, emit = collect()
    d = make_delib(flaky, scripted_foreman(
        [{"action": "call_on", "target": s} for s in range(1, 13)]), emit,
        tmp_path, cap=12)
    verdict = d.run()
    assert verdict == "hung"               # turn cap, never aborted


def test_foreman_fixation_is_spread_across_seats(tmp_path):
    """A foreman that keeps naming the same seat must not let it monopolize
    the floor — the cooldown reroutes to quieter jurors."""
    events, emit = collect()
    def always_8(_transcript, _last_tally, _turn, _turn_cap):
        return {"action": "call_on", "target": 8}
    d = make_delib(split_juror({1}), always_8, emit, tmp_path, cap=5)
    d.run()
    seats = [e["seat"] for e in events if e["type"] == "speech"]
    assert len(seats) == 5
    # no juror speaks twice within SPEAKER_COOLDOWN consecutive turns, even
    # though the foreman named the same seat every time
    from orchestrator import SPEAKER_COOLDOWN
    for i in range(len(seats)):
        window = seats[max(0, i - (SPEAKER_COOLDOWN - 1)):i]
        assert seats[i] not in window
    assert len(set(seats)) >= 4         # the floor was genuinely spread


def test_vote_result_carries_per_seat_reasoning(tmp_path):
    def reasoning_juror(card, _case_text, _transcript, mode, _last_tally=None):
        if mode == "vote":
            return {"reasoning": f"seat {card['seat']} thinking",
                    "vote": "guilty" if card["seat"] != 1 else "not_guilty"}
        return {"speech": "x", "lean": "undecided", "confidence": 0.5}
    events, emit = collect()
    d = make_delib(reasoning_juror, scripted_foreman([]), emit, tmp_path)
    d._call_vote()
    result = next(e for e in events if e["type"] == "vote_result")
    assert result["reasons"][3] == "seat 3 thinking"
    assert set(result["reasons"]) == set(range(1, 13))
    assert d.last_vote_reasons[1] == "seat 1 thinking"


def test_speech_flags_reconsidering_against_last_public_vote(tmp_path):
    def flips(_card, _case_text, _transcript, mode, _last_tally=None):
        if mode == "vote":
            return {"vote": "guilty"}
        return {"speech": "changing my mind", "lean": "not_guilty",
                "confidence": 0.6}
    events, emit = collect()
    d = make_delib(flips, scripted_foreman([]), emit, tmp_path)
    d._call_vote()                     # seat 5 publicly votes guilty
    events.clear()
    d._call_on(5)                      # then speaks with a not_guilty lean
    speech = next(e for e in events if e["type"] == "speech")
    assert speech["reconsidering"] is True


def test_speech_not_reconsidering_when_lean_matches_vote(tmp_path):
    def steady(_card, _case_text, _transcript, mode, _last_tally=None):
        if mode == "vote":
            return {"vote": "guilty"}
        return {"speech": "still sure", "lean": "guilty", "confidence": 0.9}
    events, emit = collect()
    d = make_delib(steady, scripted_foreman([]), emit, tmp_path)
    d._call_vote()
    events.clear()
    d._call_on(5)
    speech = next(e for e in events if e["type"] == "speech")
    assert speech["reconsidering"] is False


def test_speech_not_reconsidering_before_any_public_vote(tmp_path):
    def fn(_card, _case_text, _transcript, _mode, _last_tally=None):
        return {"speech": "first thoughts", "lean": "not_guilty",
                "confidence": 0.4}
    events, emit = collect()
    d = make_delib(fn, scripted_foreman([]), emit, tmp_path)
    d._call_on(5)                      # no vote has happened yet
    speech = events[-1]
    assert speech["reconsidering"] is False


def test_transcript_written_without_audio(tmp_path):
    def audible(card, _case_text, _transcript, mode, _last_tally=None):
        if mode == "vote":
            # not unanimous, so the opening ballot doesn't end the run
            return {"vote": "guilty" if card["seat"] != 1 else "not_guilty"}
        return {"speech": "hi", "lean": "guilty", "confidence": 0.5,
                "audio": "data:audio/mpeg;base64,HUGE"}
    events, emit = collect()
    d = make_delib(audible, scripted_foreman(
        [{"action": "call_on", "target": 1}]), emit, tmp_path, cap=1)
    d.run()
    path = tmp_path / "test.json"
    assert path.exists()
    saved = json.loads(path.read_text())
    assert any(e["type"] == "speech" for e in saved)
    assert all("audio" not in e for e in saved)


def test_should_stop_ends_run_between_turns(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([{"action": "call_on", "target": s}
                                for s in range(1, 13)])
    d = Deliberation("CASE", CARDS, split_juror({1, 2, 3}), foreman, emit,
                     turn_cap=200, transcript_dir=tmp_path, run_id="test",
                     should_stop=lambda: True)
    assert d.run() == "stopped"
    assert events[-1] == {"type": "verdict", "verdict": "stopped",
                          "reason": "stopped by operator"}
    speeches = [e for e in events if e["type"] == "speech"]
    assert speeches == []              # stopped before any discussion turn


def test_force_vote_when_foreman_never_calls_one(tmp_path):
    """A foreman that only ever picks call_on must not stall the room
    forever — the orchestrator forces a re-vote after FORCE_VOTE_EVERY
    discussion turns, independent of what the foreman decides."""
    def never_votes(_transcript, _last_tally, _turn, _turn_cap):
        return {"action": "call_on", "target": 8}
    events, emit = collect()
    d = make_delib(split_juror({1, 2, 3}), never_votes, emit, tmp_path,
                   cap=50)
    d.run()
    vote_called_turns = [i for i, e in enumerate(events)
                        if e["type"] == "vote_called"]
    # opening ballot (index ~2) plus at least one forced re-vote before
    # the 50-turn cap, despite the foreman never choosing to vote
    assert len(vote_called_turns) >= 2


def test_force_vote_resets_after_a_vote(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman(
        [{"action": "call_vote"}] +                # right after opening: bounced
        [{"action": "call_on", "target": 8}] * 30)  # then just discussion
    d = make_delib(split_juror({1, 2, 3}), foreman, emit, tmp_path, cap=20)
    d.run()
    types = [e["type"] for e in events]
    # forced re-vote should fire once turns_since_vote hits FORCE_VOTE_EVERY,
    # not immediately after the opening ballot
    first_forced = types.index("vote_called", types.index("vote_called") + 1)
    speeches_before = types[:first_forced].count("speech")
    assert speeches_before >= 15   # FORCE_VOTE_EVERY, not fewer
