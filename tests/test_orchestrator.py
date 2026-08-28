"""Deliberation state machine.

The room is run by agents, not by rules in this file: the foreman's choice of
speaker is always honored, no ballot is forced on anyone, when an exhibit is
finished is his call, and a declared verdict is the judge's to take or refuse.
What is still enforced in code is bookkeeping, the operator's stop, and the
guards that stop a broken run — the turn cap, the per-exhibit runaway cap and
the consecutive-failure abort.
"""

import json
import threading

import pytest

from orchestrator import (EXHIBIT_TURN_CAP, FAIL_CAP, POSITIONS, Deliberation,
                          Stopped, position_summary)

CARDS = [
    {"id": f"juror_{n:02d}", "seat": n, "name": f"J{n}", "occupation": f"job{n}",
     "temperament": "t", "biases": "b", "speech_style": "s"}
    for n in range(1, 13)
]

EXHIBITS = [
    {"id": "the_knife", "name": "The switchblade",
     "prosecution_claim": "one of a kind", "record": "carved handle"},
    {"id": "the_old_man", "name": "The old man downstairs",
     "prosecution_claim": "he heard the shout", "record": "he drags a leg"},
]

CASE = {"id": "the_stabbing", "title": "The State v. the Defendant",
        "charge": "First-degree murder.", "narrative": "THE FULL RECORD",
        "exhibits": EXHIBITS}


def collect():
    events = []
    return events, events.append


def types_of(events):
    return [e["type"] for e in events]


def seats_that_spoke(events):
    return [e["seat"] for e in events if e["type"] == "speech"]


def first(events, kind):
    return next(e for e in events if e["type"] == kind)


def only(events, kind):
    return [e for e in events if e["type"] == kind]


def make_juror(speech="I think...", lean="undecided", vote="guilty",
               action=None):
    def fn(_card, _case, _transcript, mode, _tally=None, _note=None,
           _method=None, _exhibit=None, _findings=None):
        if mode == "speak":
            reply = {"speech": speech, "lean": lean, "confidence": 0.5}
            if action is not None:
                reply["action"] = action
            return reply
        return {"vote": vote}
    return fn


def split_juror(guilty_seats):
    """Jurors in guilty_seats vote guilty, the rest not_guilty."""
    def fn(card, _case, _transcript, mode, _tally=None, _note=None,
           _method=None, _exhibit=None, _findings=None):
        if mode == "speak":
            return {"speech": f"J{card['seat']} speaks.",
                    "lean": "undecided", "confidence": 0.5}
        return {"vote": "guilty" if card["seat"] in guilty_seats
                else "not_guilty"}
    return fn


def make_assessor(position="inconclusive"):
    """Every juror reads the exhibit the same way, and records what it saw."""
    calls = []

    def fn(card, case, exhibit, findings):
        calls.append({"seat": card["seat"], "case": case, "exhibit": exhibit,
                      "findings": list(findings or [])})
        return {"position": position, "reasoning": f"seat {card['seat']} says",
                "confidence": 0.5}
    fn.calls = calls
    return fn


def scripted_foreman(actions):
    """Replays actions in order; keeps returning the last one afterwards so a
    test only has to script the part it cares about."""
    state = {"i": 0, "calls": []}

    def fn(case, transcript, last_tally, turn, turn_cap, pending=None,
           speech_counts=None, judge_note=None, exhibit=None, findings=None,
           exhibit_turns=0, remaining=0):
        state["calls"].append({
            "case": case, "transcript": list(transcript),
            "last_tally": last_tally, "turn": turn, "turn_cap": turn_cap,
            "pending": pending, "speech_counts": speech_counts,
            "judge_note": judge_note, "exhibit": exhibit,
            "findings": list(findings or []), "exhibit_turns": exhibit_turns,
            "remaining": remaining})
        if not actions:
            raise AssertionError("foreman called with no scripted action")
        i = min(state["i"], len(actions) - 1)
        state["i"] += 1
        return actions[i]
    fn.calls = state["calls"]
    return fn


HUNG = {"action": "declare", "verdict": "hung", "reason": "deadlock"}
CLOSE = {"action": "close_exhibit", "finding": "nothing turns on it"}


def make_delib(juror_fn, foreman_fn, emit, tmp_path, cap=200, case=CASE, **kw):
    return Deliberation(case, CARDS, juror_fn, foreman_fn, emit,
                        turn_cap=cap, transcript_dir=tmp_path, run_id="test",
                        **kw)


# --- the run opens with the case, the room and the docket ------------------

def test_the_run_opens_with_the_case_the_roster_and_the_docket(tmp_path):
    events, emit = collect()
    d = make_delib(split_juror({1}), scripted_foreman([HUNG]), emit, tmp_path)
    d.run()
    assert types_of(events)[:3] == ["case", "roster", "docket"]
    assert events[0] == {"type": "case", "text": "THE FULL RECORD",
                         "title": CASE["title"], "charge": CASE["charge"]}
    roster = events[1]
    assert [j["seat"] for j in roster["jurors"]] == list(range(1, 13))
    assert roster["jurors"][0]["name"] == "J1"
    docket = events[2]
    assert docket["exhibits"] == [
        {"id": "the_knife", "name": "The switchblade",
         "claim": "one of a kind"},
        {"id": "the_old_man", "name": "The old man downstairs",
         "claim": "he heard the shout"}]
    # the docket announcement is a summary — the exhibit's own record text is
    # only shown when it comes before the room
    assert all("record" not in e for e in docket["exhibits"])


def test_the_first_exhibit_is_put_in_front_of_the_room_before_any_turn(
        tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([{"action": "call_on", "target": 4}, HUNG])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path)
    d.run()
    assert types_of(events)[:4] == ["case", "roster", "docket", "exhibit"]
    assert events[3] == {"type": "exhibit", "id": "the_knife",
                         "name": "The switchblade", "claim": "one of a kind",
                         "record": "carved handle", "index": 0, "total": 2}
    assert foreman.calls[0]["exhibit"] == EXHIBITS[0]
    assert foreman.calls[0]["remaining"] == 1


def test_no_opening_ballot_is_forced_on_the_room(tmp_path):
    """Whether this jury starts with a show of hands is the foreman's call."""
    events, emit = collect()
    d = make_delib(split_juror({1}),
                   scripted_foreman([{"action": "call_on", "target": 7},
                                     HUNG]), emit, tmp_path)
    d.run()
    assert "vote_called" not in types_of(events)


# --- opening an exhibit: twelve independent reads --------------------------

def test_opening_an_exhibit_polls_all_twelve_before_a_word_is_spoken(tmp_path):
    events, emit = collect()
    assessor = make_assessor("raises_doubt")
    d = make_delib(split_juror({1}), scripted_foreman([HUNG]), emit, tmp_path,
                   assess_fn=assessor)
    d.run()
    kinds = types_of(events)
    assert kinds.count("assessed") == 12
    assert kinds.count("positions") == 1
    # every read is in before the room is told where it stands
    assert kinds.index("positions") > max(
        i for i, k in enumerate(kinds) if k == "assessed")
    assert {e["seat"] for e in only(events, "assessed")} == set(range(1, 13))
    assert all(e["exhibit"] == "the_knife" for e in only(events, "assessed"))
    positions = first(events, "positions")
    assert positions["exhibit"] == "the_knife"
    assert positions["positions"] == {s: "raises_doubt" for s in range(1, 13)}
    assert positions["reasons"][7] == "seat 7 says"
    assert positions["summary"] == "0 for the prosecution, 12 doubting it, 0 unmoved"
    assert d.positions["the_knife"][1] == "raises_doubt"


def test_the_assessment_runs_concurrently(tmp_path):
    """Twelve serial assessments per exhibit is the difference between a run
    of minutes and a run of hours."""
    inside = threading.Barrier(12, timeout=5)

    def fn(card, _case, _exhibit, _findings):
        inside.wait()          # deadlocks unless all twelve are in flight
        return {"position": "supports_guilt", "reasoning": "r"}
    events, emit = collect()
    d = make_delib(split_juror({1}), scripted_foreman([HUNG]), emit, tmp_path,
                   assess_fn=fn)
    d.run()                                    # would raise BrokenBarrier
    assert first(events, "positions")["summary"].startswith("12 for")


def test_each_assessor_gets_the_case_the_one_exhibit_and_the_findings(
        tmp_path):
    events, emit = collect()
    assessor = make_assessor()
    foreman = scripted_foreman([CLOSE, HUNG])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path,
                   assess_fn=assessor)
    d.run()
    opening = assessor.calls[:12]
    assert {c["seat"] for c in opening} == set(range(1, 13))
    assert all(c["case"] is CASE for c in opening)
    assert all(c["exhibit"] == EXHIBITS[0] for c in opening)
    assert all(c["findings"] == [] for c in opening)
    # the second exhibit is read in the light of what the room already settled
    second = assessor.calls[12:]
    assert len(second) == 12
    assert all(c["exhibit"] == EXHIBITS[1] for c in second)
    assert all(c["findings"] == [{"name": "The switchblade",
                                  "summary": "nothing turns on it"}]
               for c in second)


def test_a_broken_assessment_degrades_to_inconclusive_and_counts_as_failure(
        tmp_path):
    def half_broken(card, _case, _exhibit, _findings):
        if card["seat"] <= 4:
            raise RuntimeError("backend down")
        if card["seat"] == 5:
            return "not a dict at all"
        return {"position": "supports_guilt", "reasoning": "r"}
    events, emit = collect()
    d = make_delib(split_juror({1}), scripted_foreman([HUNG]), emit, tmp_path,
                   assess_fn=half_broken)
    assert d.run() == "hung"                 # the room carries on regardless
    positions = first(events, "positions")["positions"]
    assert [positions[s] for s in range(1, 6)] == ["inconclusive"] * 5
    assert positions[6] == "supports_guilt"


def test_a_bad_assessment_round_counts_as_one_agent_failure(tmp_path):
    events, emit = collect()
    d = make_delib(split_juror({1}), scripted_foreman([HUNG]), emit, tmp_path,
                   assess_fn=lambda *_a: None)
    d._open_exhibit()
    assert d._fails["assessment"] == 1       # one bad round, not twelve


def test_an_assessment_with_an_unusable_position_is_not_an_agent_failure(
        tmp_path):
    """The agent answered; it just answered off-schema. That degrades, but it
    is not the dead-backend signal that aborts a run."""
    events, emit = collect()
    d = make_delib(split_juror({1}), scripted_foreman([HUNG]), emit, tmp_path,
                   assess_fn=lambda *_a: {"position": "he's guilty as sin",
                                          "reasoning": "r"})
    d._open_exhibit()
    assert set(first(events, "positions")["positions"].values()) == {
        "inconclusive"}
    assert "assessment" not in d._fails


def test_a_dead_assessor_eventually_aborts_the_run(tmp_path):
    events, emit = collect()
    many = {**CASE, "exhibits": [{**EXHIBITS[0], "id": f"ex{i}"}
                                 for i in range(FAIL_CAP + 2)]}
    d = make_delib(split_juror({1}), scripted_foreman([CLOSE]), emit, tmp_path,
                   case=many, assess_fn=lambda *_a: None)
    for _ in range(FAIL_CAP):
        d._open_exhibit()
        d.exhibit_idx += 1
    assert d.verdict == "aborted"
    assert "consecutive assessment failures" in first(events, "error")["message"]
    assert "assessment" in first(events, "error")["message"]


def test_without_an_assessor_the_exhibit_still_comes_before_the_room(tmp_path):
    events, emit = collect()
    d = make_delib(split_juror({1}), scripted_foreman([HUNG]), emit, tmp_path)
    d.run()
    assert first(events, "exhibit")["id"] == "the_knife"
    assert "assessed" not in types_of(events)
    assert "positions" not in types_of(events)


def test_assessment_traces_reach_the_operator_panel(tmp_path):
    events, emit = collect()
    d = make_delib(split_juror({1}), scripted_foreman([HUNG]), emit, tmp_path,
                   assess_fn=lambda card, *_a: {
                       "position": "raises_doubt", "reasoning": "r",
                       "_prompt": {"system": "s", "user": "u"},
                       "_raw_output": "{}"})
    d.run()
    assert len(only(events, "prompt")) == 12
    assert all(e["mode"] == "assess" for e in only(events, "reasoning"))


# --- position_summary ------------------------------------------------------

def test_position_summary_phrases_where_the_room_came_out():
    assert position_summary({1: "supports_guilt", 2: "supports_guilt",
                             3: "raises_doubt", 4: "inconclusive"}) == \
        "2 for the prosecution, 1 doubting it, 1 unmoved"


def test_position_summary_ignores_values_that_are_not_positions():
    assert position_summary({1: "supports_guilt", 2: "banana", 3: None}) == \
        "1 for the prosecution, 0 doubting it, 0 unmoved"


def test_position_summary_of_an_unread_exhibit():
    assert position_summary({}) == \
        "0 for the prosecution, 0 doubting it, 0 unmoved"


def test_every_position_the_orchestrator_accepts_is_offered_in_the_prompt():
    import prompts
    assert POSITIONS == prompts.POSITIONS


# --- closing exhibits and working the docket -------------------------------

def test_closing_an_exhibit_records_a_finding_and_opens_the_next(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([CLOSE, {"action": "call_on", "target": 2},
                                HUNG])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path,
                   assess_fn=make_assessor())
    d.run()
    closed = first(events, "exhibit_closed")
    assert closed == {"type": "exhibit_closed", "id": "the_knife",
                      "name": "The switchblade",
                      "finding": "nothing turns on it"}
    assert d.findings == [{"id": "the_knife", "name": "The switchblade",
                           "summary": "nothing turns on it"}]
    opened = only(events, "exhibit")
    assert [e["id"] for e in opened] == ["the_knife", "the_old_man"]
    assert opened[1]["index"] == 1
    # the next exhibit is read fresh, before the room argues it
    kinds = types_of(events)
    assert kinds.index("exhibit_closed") < kinds.index("speaker")
    # the counter restarts per exhibit: three foreman turns ran in total, but
    # only the two that happened while the second exhibit was open count
    assert d.turn == 3
    assert d.exhibit_turns == 2


def test_a_finding_the_foreman_leaves_blank_falls_back_to_the_count(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([{"action": "close_exhibit"}, HUNG])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path,
                   assess_fn=make_assessor("supports_guilt"))
    d.run()
    assert first(events, "exhibit_closed")["finding"] == \
        "12 for the prosecution, 0 doubting it, 0 unmoved"


def test_the_last_exhibit_closes_the_docket(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([CLOSE, CLOSE, HUNG])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path,
                   assess_fn=make_assessor())
    assert d.run() == "hung"
    assert len(only(events, "exhibit_closed")) == 2
    closed = first(events, "docket_closed")
    assert [f["id"] for f in closed["findings"]] == ["the_knife",
                                                     "the_old_man"]
    kinds = types_of(events)
    assert kinds.index("docket_closed") > kinds.index("exhibit_closed")
    assert "exhibit" not in kinds[kinds.index("docket_closed"):]
    assert d.exhibit is None


def test_findings_accumulate_into_every_later_prompt(tmp_path):
    events, emit = collect()
    seen = []

    def fn(_card, _case, _transcript, mode, _tally=None, _note=None,
           _method=None, exhibit=None, findings=None):
        seen.append({"mode": mode, "exhibit": exhibit,
                     "findings": list(findings or [])})
        if mode == "speak":
            return {"speech": "x", "lean": "guilty", "confidence": 0.5}
        return {"vote": "guilty"}
    foreman = scripted_foreman([
        {"action": "call_on", "target": 1},
        CLOSE,
        {"action": "call_on", "target": 2},
        {"action": "call_vote"},
        HUNG])
    d = make_delib(fn, foreman, emit, tmp_path)
    d.run()
    assert seen[0] == {"mode": "speak", "exhibit": EXHIBITS[0],
                       "findings": []}
    settled = [{"name": "The switchblade", "summary": "nothing turns on it"}]
    assert seen[1] == {"mode": "speak", "exhibit": EXHIBITS[1],
                       "findings": settled}
    assert all(s["findings"] == settled and s["exhibit"] == EXHIBITS[1]
               for s in seen[2:])           # the ballot sees them too
    assert foreman.calls[2]["findings"] == settled
    assert foreman.calls[2]["exhibit"] == EXHIBITS[1]
    assert foreman.calls[2]["remaining"] == 0


def test_closing_an_exhibit_when_the_docket_is_done_falls_back_to_a_speaker(
        tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([CLOSE, CLOSE, CLOSE, HUNG])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path)
    assert d.run() == "hung"
    assert len(only(events, "exhibit_closed")) == 2
    assert seats_that_spoke(events) == [1]          # the stray close, absorbed
    assert foreman.calls[2]["exhibit"] is None


def test_the_foreman_is_told_how_long_the_open_exhibit_has_run(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([{"action": "call_on", "target": 5}] * 3
                               + [HUNG])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path)
    d.run()
    assert [c["exhibit_turns"] for c in foreman.calls[:4]] == [0, 1, 2, 3]


def test_a_runaway_exhibit_is_taken_away_from_the_room(tmp_path):
    """A guard, not a pacing rule: the foreman is expected to close exhibits
    long before this bites, but a room that argues one forever never reaches
    a verdict."""
    events, emit = collect()
    foreman = scripted_foreman([{"action": "call_on", "target": 3}])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path, cap=40)
    assert d.run() == "hung"
    error = first(events, "error")
    assert f"ran past {EXHIBIT_TURN_CAP} turns" in error["message"]
    assert len(only(events, "exhibit_closed")) == 2
    assert "docket_closed" in types_of(events)
    # forced closes happen exactly on the cap, once per exhibit
    assert [len(only(events[:i], "speech"))
            for i, e in enumerate(events) if e["type"] == "exhibit_closed"] == \
        [EXHIBIT_TURN_CAP, EXHIBIT_TURN_CAP * 2]


def test_a_forced_close_still_records_what_the_room_thought(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([{"action": "call_on", "target": 3}])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path, cap=10,
                   assess_fn=make_assessor("supports_guilt"))
    d.run()
    assert first(events, "exhibit_closed")["finding"] == \
        "12 for the prosecution, 0 doubting it, 0 unmoved"


def test_every_turn_on_an_open_exhibit_counts_against_its_cap(tmp_path):
    """A foreman looping on ballots is exactly as stuck as one looping on
    speakers. Counting only speaking turns let him circle forever without the
    guard ever seeing it."""
    events, emit = collect()
    foreman = scripted_foreman([{"action": "call_vote"}] * 12 + [HUNG])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path, cap=13)
    d.run()
    assert "exhibit_closed" in types_of(events)
    assert EXHIBIT_TURN_CAP in (8,)          # the cap this pins
    assert "the court moves the room on" in first(events, "error")["message"]


# --- a juror speaks --------------------------------------------------------

def test_call_on_records_speech_and_keeps_the_lean_private(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(speech="hello", lean="not_guilty"),
                   scripted_foreman([HUNG]), emit, tmp_path)
    d._call_on(5)
    assert types_of(events) == ["speaker", "speech"]
    assert events[1] == {"type": "speech", "seat": 5, "name": "J5",
                         "speech": "hello", "reconsidering": False}
    assert d.leans[5] == {"lean": "not_guilty", "confidence": 0.5}
    assert d.transcript == [{"seat": 5, "name": "J5", "speech": "hello"}]
    # every seat is present from the start — the counts double as the roster
    # the foreman is told he may call on
    assert d.speech_counts[5] == 1
    assert sorted(d.speech_counts) == list(range(1, 13))
    assert all(n == 0 for s, n in d.speech_counts.items() if s != 5)


def test_call_on_juror_failure_becomes_a_pass(tmp_path):
    def broken(*_a, **_kw):
        raise RuntimeError("llm died")
    events, emit = collect()
    d = make_delib(broken, scripted_foreman([HUNG]), emit, tmp_path)
    d._call_on(2)
    assert events[-1]["type"] == "speech"
    assert "passes" in events[-1]["speech"]
    assert d.transcript == []          # a pass is not part of the record


@pytest.mark.parametrize("reply", ["just a string", None,
                                   {"lean": "guilty"}, {"speech": ""}])
def test_a_juror_answering_off_schema_has_failed_just_as_completely(
        reply, tmp_path):
    events, emit = collect()
    d = make_delib(lambda *_a: reply, scripted_foreman([HUNG]), emit, tmp_path)
    d._call_on(2)
    assert "passes" in events[-1]["speech"]
    assert d.transcript == []
    assert d._fails["juror"] == 1


def test_call_on_passes_the_speaking_mode_the_case_and_the_exhibit(tmp_path):
    seen = {}

    def fn(card, case, transcript, mode, tally, note, method, exhibit,
           findings):
        seen.update(card=card, case=case, transcript=transcript, mode=mode,
                    tally=tally, note=note, method=method, exhibit=exhibit,
                    findings=findings)
        return {"speech": "x", "lean": "guilty", "confidence": 0.1}
    events, emit = collect()
    d = make_delib(fn, scripted_foreman([HUNG]), emit, tmp_path)
    d._open_exhibit()
    d._call_on(4)
    assert seen["mode"] == "speak"
    assert seen["case"] is CASE
    assert seen["method"] is None
    assert seen["exhibit"] == EXHIBITS[0]
    assert seen["findings"] == []
    assert seen["card"]["seat"] == 4


def test_private_leans_never_reach_an_event(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(lean="guilty"), scripted_foreman([HUNG]), emit,
                   tmp_path)
    d._call_on(1)
    d._call_vote()
    blob = json.dumps(events)
    assert "lean" not in blob
    assert "confidence" not in blob


def test_trace_events_carry_the_prompt_and_raw_output(tmp_path):
    def traced(_card, _case, _transcript, mode, *_a):
        base = {"_prompt": {"system": "sys", "user": "usr"},
                "_raw_output": "{...}"}
        if mode == "speak":
            return {**base, "speech": "x", "lean": "guilty", "confidence": 0.2}
        return {**base, "vote": "guilty"}
    events, emit = collect()
    d = make_delib(traced, scripted_foreman([HUNG]), emit, tmp_path)
    d._call_on(6)
    prompt = first(events, "prompt")
    assert prompt["seat"] == 6 and prompt["system"] == "sys"
    reasoning = first(events, "reasoning")
    assert reasoning["mode"] == "speak" and reasoning["raw"] == "{...}"
    events.clear()
    d._call_vote()
    assert first(events, "reasoning")["mode"] == "vote"


# --- reconsidering ---------------------------------------------------------

def test_speech_flags_reconsidering_against_the_last_public_vote(tmp_path):
    def flips(_card, _case, _transcript, mode, *_a):
        if mode == "vote":
            return {"vote": "guilty"}
        return {"speech": "changing my mind", "lean": "not_guilty",
                "confidence": 0.6}
    events, emit = collect()
    d = make_delib(flips, scripted_foreman([HUNG]), emit, tmp_path)
    d._call_vote()
    events.clear()
    d._call_on(5)
    assert first(events, "speech")["reconsidering"] is True


def test_speech_not_reconsidering_when_the_lean_matches_the_vote(tmp_path):
    def steady(_card, _case, _transcript, mode, *_a):
        if mode == "vote":
            return {"vote": "guilty"}
        return {"speech": "still sure", "lean": "guilty", "confidence": 0.9}
    events, emit = collect()
    d = make_delib(steady, scripted_foreman([HUNG]), emit, tmp_path)
    d._call_vote()
    events.clear()
    d._call_on(5)
    assert first(events, "speech")["reconsidering"] is False


def test_speech_not_reconsidering_before_any_public_vote(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(lean="not_guilty"), scripted_foreman([HUNG]),
                   emit, tmp_path)
    d._call_on(5)
    assert events[-1]["reconsidering"] is False


# --- the ballot ------------------------------------------------------------

def test_call_vote_polls_all_twelve_and_tallies(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(vote="guilty"), scripted_foreman([HUNG]), emit,
                   tmp_path)
    d._call_vote()
    assert types_of(events)[0] == "vote_called"
    assert types_of(events).count("voter_done") == 12
    result = first(events, "vote_result")
    assert len(result["votes"]) == 12
    assert result["tally"]["guilty"] == 12


def test_a_unanimous_ballot_does_not_end_the_case_by_itself(tmp_path):
    """The count is not the verdict — the foreman still has to take it up."""
    events, emit = collect()
    d = make_delib(make_juror(vote="not_guilty"), scripted_foreman([HUNG]),
                   emit, tmp_path)
    d._call_vote(binding=True)
    assert d.verdict is None
    assert "verdict" not in types_of(events)


def test_call_vote_reports_method_and_binding_on_both_events(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(), scripted_foreman([HUNG]), emit, tmp_path)
    d._call_vote(method="secret", binding=False)
    called = first(events, "vote_called")
    assert called["method"] == "secret" and called["binding"] is False
    result = first(events, "vote_result")
    assert result["method"] == "secret" and result["binding"] is False
    assert result["secret"] is True
    assert d.last_method == "secret"


def test_a_show_of_hands_is_not_secret(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(), scripted_foreman([HUNG]), emit, tmp_path)
    d._call_vote(method="hands")
    result = first(events, "vote_result")
    assert result["secret"] is False
    assert result["method"] == "hands"


def test_the_ballot_method_exhibit_and_findings_reach_each_juror(tmp_path):
    seen = []

    def fn(_card, _case, _transcript, mode, _tally, _note, method, exhibit,
           findings):
        seen.append((mode, method, exhibit, list(findings or [])))
        return {"vote": "guilty"}
    events, emit = collect()
    d = make_delib(fn, scripted_foreman([HUNG]), emit, tmp_path)
    d._open_exhibit()
    d._call_vote(method="secret")
    assert seen == [("vote", "secret", EXHIBITS[0], [])] * 12


def test_a_secret_ballot_hides_who_voted_what_but_not_the_count(tmp_path):
    events, emit = collect()
    d = make_delib(split_juror({1, 2, 3}), scripted_foreman([HUNG]), emit,
                   tmp_path)
    d._call_vote(method="secret")
    result = first(events, "vote_result")
    assert result["votes"] == {}           # the room sees no hands
    assert result["tally"] == {"guilty": 3, "not_guilty": 9, "undecided": 0,
                               "abstain": 0}
    # reasoning still reaches the operator's trace panel
    assert set(result["reasons"]) == set(range(1, 13))
    # and the orchestrator itself still knows, for change_vote bookkeeping
    assert d.last_votes[1] == "guilty"
    assert d.last_votes[12] == "not_guilty"


def test_an_abstention_stands_on_a_secret_ballot(tmp_path):
    def abstainer(card, _case, _transcript, _mode, *_a):
        return {"vote": "abstain" if card["seat"] == 8 else "guilty"}
    events, emit = collect()
    d = make_delib(abstainer, scripted_foreman([HUNG]), emit, tmp_path)
    d._call_vote(method="secret")
    result = first(events, "vote_result")
    assert result["tally"]["abstain"] == 1
    assert result["tally"]["guilty"] == 11
    assert d.last_votes[8] == "abstain"


def test_no_hiding_in_a_show_of_hands(tmp_path):
    def abstainer(card, _case, _transcript, _mode, *_a):
        return {"vote": "abstain" if card["seat"] == 8 else "guilty"}
    events, emit = collect()
    d = make_delib(abstainer, scripted_foreman([HUNG]), emit, tmp_path)
    d._call_vote(method="hands")
    result = first(events, "vote_result")
    assert result["tally"]["abstain"] == 0
    assert result["tally"]["undecided"] == 1     # downgraded, not honored
    assert d.last_votes[8] == "undecided"


def test_invalid_or_failing_votes_count_undecided(tmp_path):
    def flaky(card, _case, _transcript, _mode, *_a):
        if card["seat"] == 1:
            raise RuntimeError("dead")
        if card["seat"] == 2:
            return {"vote": "banana"}
        if card["seat"] == 3:
            return "not a dict"
        return {"vote": "guilty"}
    events, emit = collect()
    d = make_delib(flaky, scripted_foreman([HUNG]), emit, tmp_path)
    d._call_vote()
    assert first(events, "vote_result")["tally"] == {
        "guilty": 9, "not_guilty": 0, "undecided": 3, "abstain": 0}


def test_vote_result_carries_per_seat_reasoning(tmp_path):
    def reasoning_juror(card, _case, _transcript, mode, *_a):
        if mode == "vote":
            return {"reasoning": f"seat {card['seat']} thinking",
                    "vote": "guilty"}
        return {"speech": "x", "lean": "undecided", "confidence": 0.5}
    events, emit = collect()
    d = make_delib(reasoning_juror, scripted_foreman([HUNG]), emit, tmp_path)
    d._call_vote()
    result = first(events, "vote_result")
    assert result["reasons"][3] == "seat 3 thinking"
    assert set(result["reasons"]) == set(range(1, 13))
    assert d.last_vote_reasons[1] == "seat 1 thinking"


# --- the foreman runs the room --------------------------------------------

def test_the_foremans_choice_of_speaker_is_always_honored(tmp_path):
    """No rotation rule overrides him: if he wants seat 8 every turn, seat 8
    speaks every turn."""
    events, emit = collect()
    foreman = scripted_foreman([{"action": "call_on", "target": 8}])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path, cap=5)
    assert d.run() == "hung"
    assert seats_that_spoke(events) == [8] * 5


def test_the_foreman_may_call_ballots_back_to_back(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([{"action": "call_vote"},
                                {"action": "call_vote", "method": "secret"},
                                HUNG])
    d = make_delib(split_juror({1, 2, 3}), foreman, emit, tmp_path)
    d.run()
    called = only(events, "vote_called")
    assert len(called) == 2
    assert [e["method"] for e in called] == ["hands", "secret"]
    assert "speech" not in types_of(events)


def test_call_vote_defaults_to_a_binding_show_of_hands(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([{"action": "call_vote"}, HUNG])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path)
    d.run()
    called = first(events, "vote_called")
    assert called["method"] == "hands"
    assert called["binding"] is True


def test_an_unknown_ballot_method_falls_back_to_a_show_of_hands(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([{"action": "call_vote", "method": "telepathy"},
                                HUNG])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path)
    d.run()
    assert first(events, "vote_called")["method"] == "hands"


def test_a_non_binding_straw_poll_is_marked_as_such(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([{"action": "call_vote", "binding": False},
                                HUNG])
    d = make_delib(make_juror(vote="guilty"), foreman, emit, tmp_path)
    d.run()
    assert first(events, "vote_result")["binding"] is False


def test_the_foreman_sees_the_room_he_is_running(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([{"action": "call_on", "target": 8}, HUNG])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path)
    d.run()
    first_call, second_call = foreman.calls[0], foreman.calls[1]
    assert first_call["case"] is CASE
    assert first_call["turn"] == 1 and first_call["turn_cap"] == 200
    # the whole roster, at zero, before anyone has spoken
    assert first_call["speech_counts"] == {s: 0 for s in range(1, 13)}
    assert first_call["pending"] == []
    assert first_call["judge_note"] is None
    assert first_call["findings"] == []
    assert second_call["speech_counts"][8] == 1
    assert second_call["transcript"][0]["seat"] == 8


def test_a_foreman_failure_falls_back_to_the_quietest_juror(tmp_path):
    calls = {"n": 0}

    def flaky_foreman(*_a, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("malformed")
        return HUNG
    events, emit = collect()
    d = make_delib(split_juror({1, 2}), flaky_foreman, emit, tmp_path)
    assert d.run() == "hung"
    assert seats_that_spoke(events) == [1]


def test_a_foreman_answering_off_schema_falls_back_too(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman(["call_on 8", HUNG])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path)
    assert d.run() == "hung"
    assert seats_that_spoke(events) == [1]


def test_an_unusable_foreman_action_errors_and_falls_back(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([{"action": "adjourn_for_lunch"}, HUNG])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path)
    assert d.run() == "hung"
    error = first(events, "error")
    assert "unusable action" in error["message"]
    assert seats_that_spoke(events) == [1]


def test_call_on_an_empty_chair_is_unusable(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([{"action": "call_on", "target": 99}, HUNG])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path)
    d.run()
    # named as the specific mistake it is, not lumped in with unparseable JSON
    assert "not seated on this jury" in first(events, "error")["message"]
    assert seats_that_spoke(events) == [1]


def test_the_fallback_speaker_is_the_least_recently_heard(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([
        {"action": "call_on", "target": 1},
        {"action": "call_on", "target": 2},
        {"action": "nonsense"},            # falls back
        HUNG])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path)
    d.run()
    assert seats_that_spoke(events) == [1, 2, 3]  # 1 and 2 spoke, 3 has not


# --- jurors act on the room ------------------------------------------------

def test_a_juror_request_lands_in_pending_and_is_announced(tmp_path):
    events, emit = collect()
    action = {"type": "request_evidence", "item": "the murder weapon"}
    d = make_delib(make_juror(action=action), scripted_foreman([HUNG]), emit,
                   tmp_path)
    d._call_on(9)
    assert d.pending == {9: action}
    request = first(events, "request")
    assert request["seat"] == 9
    assert request["kind"] == "request_evidence"
    assert "the murder weapon" in request["summary"]
    assert types_of(events).index("request") > types_of(events).index("speech")


def test_a_juror_has_only_one_request_on_the_table_at_a_time(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(action={"type": "demand_vote",
                                      "method": "secret"}),
                   scripted_foreman([HUNG]), emit, tmp_path)
    d._call_on(9)
    d.juror_fn = make_juror(action={"type": "request_evidence", "item": "map"})
    d._call_on(9)
    assert list(d.pending) == [9]
    assert d.pending[9]["type"] == "request_evidence"


@pytest.mark.parametrize("action", [None, {"type": "none"}, "speak louder",
                                    {"type": "adjourn"}, {}])
def test_a_juror_with_nothing_to_ask_leaves_no_request(action, tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(action=action), scripted_foreman([HUNG]), emit,
                   tmp_path)
    d._call_on(9)
    assert d.pending == {}
    assert "request" not in types_of(events)


def test_the_foreman_grants_a_demanded_ballot(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([
        {"action": "call_on", "target": 8},
        {"action": "rule_on_request", "seat": 8, "grant": True,
         "reason": "fair enough"},
        HUNG])
    d = make_delib(make_juror(action={"type": "demand_vote",
                                      "method": "secret"}),
                   foreman, emit, tmp_path)
    d.run()
    ruling = first(events, "ruling")
    assert ruling == {"type": "ruling", "seat": 8, "granted": True,
                      "reason": "fair enough",
                      "request": "demands a vote by secret"}
    assert first(events, "vote_called")["method"] == "secret"
    assert d.pending == {}


def test_a_refused_request_produces_a_ruling_and_nothing_else(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([
        {"action": "call_on", "target": 8},
        {"action": "rule_on_request", "seat": 8, "grant": False,
         "reason": "we just voted"},
        HUNG])
    d = make_delib(make_juror(action={"type": "demand_vote"}), foreman, emit,
                   tmp_path)
    d.run()
    ruling = first(events, "ruling")
    assert ruling["granted"] is False
    assert "vote_called" not in types_of(events)
    assert d.pending == {}


def test_ruling_on_a_request_nobody_made_errors_and_falls_back(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([
        {"action": "rule_on_request", "seat": 4, "grant": True}, HUNG])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path)
    d.run()
    assert "was not open" in first(events, "error")["message"]
    assert seats_that_spoke(events) == [1]


def test_a_granted_challenge_puts_the_target_on_the_spot(tmp_path):
    notes = {}

    def fn(card, _case, _transcript, mode, _tally, note, _method, _ex=None,
           _f=None):
        if mode != "speak":
            return {"vote": "guilty"}
        notes[card["seat"]] = note
        action = ({"type": "challenge", "target": 4}
                  if card["seat"] == 8 else {"type": "none"})
        return {"speech": "well?", "lean": "guilty", "confidence": 0.5,
                "action": action}
    events, emit = collect()
    foreman = scripted_foreman([
        {"action": "call_on", "target": 8},
        {"action": "rule_on_request", "seat": 8, "grant": True,
         "reason": "answer him"},
        HUNG])
    d = make_delib(fn, foreman, emit, tmp_path)
    d.run()
    assert seats_that_spoke(events) == [8, 4]
    assert notes[8] is None
    assert "Juror #8 has put a direct question to you" in notes[4]
    assert d.floor_notes == {}          # the note is consumed once delivered


def test_a_challenge_to_an_empty_chair_goes_nowhere(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([
        {"action": "call_on", "target": 8},
        {"action": "rule_on_request", "seat": 8, "grant": True},
        HUNG])
    d = make_delib(make_juror(action={"type": "challenge", "target": 99}),
                   foreman, emit, tmp_path)
    d.run()
    assert first(events, "ruling")["granted"] is True
    assert seats_that_spoke(events) == [8]


# --- changing a vote on the floor -----------------------------------------

def test_a_juror_changes_his_vote_without_anyones_permission(tmp_path):
    events, emit = collect()
    d = make_delib(split_juror({1, 2, 3}), scripted_foreman([HUNG]), emit,
                   tmp_path)
    d._call_vote()
    events.clear()
    d.juror_fn = make_juror(action={"type": "change_vote",
                                    "vote": "not_guilty"})
    d._call_on(3)
    change = first(events, "vote_change")
    assert change["seat"] == 3 and change["vote"] == "not_guilty"
    assert change["tally"] == {"guilty": 2, "not_guilty": 10, "undecided": 0,
                               "abstain": 0}
    assert d.last_votes[3] == "not_guilty"
    assert d.last_tally == change["tally"]
    assert "ruling" not in types_of(events)   # nobody had to grant it
    assert d.pending == {}                    # it is not a request


def test_a_vote_change_before_the_first_ballot_is_a_no_op(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(action={"type": "change_vote",
                                      "vote": "not_guilty"}),
                   scripted_foreman([HUNG]), emit, tmp_path)
    d._call_on(3)
    assert "vote_change" not in types_of(events)
    assert d.last_votes == {}
    assert d.last_tally is None


def test_changing_to_the_vote_you_already_cast_changes_nothing(tmp_path):
    events, emit = collect()
    d = make_delib(split_juror({3}), scripted_foreman([HUNG]), emit, tmp_path)
    d._call_vote()
    before = d.last_tally
    events.clear()
    d.juror_fn = make_juror(action={"type": "change_vote", "vote": "guilty"})
    d._call_on(3)
    assert "vote_change" not in types_of(events)
    assert d.last_tally == before


@pytest.mark.parametrize("vote", ["abstain", "undecided", "maybe", None])
def test_an_invalid_vote_change_is_ignored(vote, tmp_path):
    events, emit = collect()
    d = make_delib(split_juror({1, 2, 3}), scripted_foreman([HUNG]), emit,
                   tmp_path)
    d._call_vote()
    events.clear()
    d.juror_fn = make_juror(action={"type": "change_vote", "vote": vote})
    d._call_on(3)
    assert "vote_change" not in types_of(events)
    assert d.last_votes[3] == "guilty"


def test_a_change_of_vote_can_make_the_room_unanimous(tmp_path):
    events, emit = collect()
    d = make_delib(split_juror({1}), scripted_foreman([HUNG]), emit, tmp_path)
    d._call_vote()
    d.juror_fn = make_juror(action={"type": "change_vote",
                                    "vote": "not_guilty"})
    d._call_on(1)
    assert d.last_tally["not_guilty"] == 12
    # ...and it still takes the foreman and the judge to end the case
    assert d.verdict is None


# --- the court officer -----------------------------------------------------

def _evidence_run(bailiff_fn, tmp_path, emit,
                  item="the floor plan of the apartment"):
    foreman = scripted_foreman([
        {"action": "call_on", "target": 8},
        {"action": "rule_on_request", "seat": 8, "grant": True,
         "reason": "send for it"},
        HUNG])
    d = Deliberation(CASE, CARDS,
                     make_juror(action={"type": "request_evidence",
                                        "item": item}),
                     foreman, emit, turn_cap=200, transcript_dir=tmp_path,
                     run_id="test", bailiff_fn=bailiff_fn)
    d.run()
    return d


def test_the_court_reads_an_exhibit_into_the_record(tmp_path):
    events, emit = collect()
    seen = {}

    def bailiff(kind, request, seat, case, transcript):
        seen.update(kind=kind, request=request, seat=seat, case=case,
                    transcript=list(transcript))
        return {"granted": True, "record": "The plan shows a 12-foot hall."}
    d = _evidence_run(bailiff, tmp_path, emit)
    assert seen["kind"] == "evidence"
    assert seen["seat"] == 8
    # the court officer is the one agent handed the whole case
    assert seen["case"] is CASE
    assert seen["case"]["narrative"] == "THE FULL RECORD"
    assert seen["request"]["item"] == "the floor plan of the apartment"
    record = first(events, "record")
    assert record == {"type": "record", "kind": "evidence", "seat": 8,
                      "available": True,
                      "text": "The plan shows a 12-foot hall."}
    # it enters the transcript as record, not as something a juror claimed
    assert d.transcript[-1] == {"kind": "record", "seat": None,
                                "name": "the court",
                                "speech": "The plan shows a 12-foot hall."}


def test_the_record_not_saying_so_is_still_an_answer(tmp_path):
    events, emit = collect()

    def bailiff(*_a):
        return {"granted": False, "record": "The record does not say."}
    d = _evidence_run(bailiff, tmp_path, emit)
    record = first(events, "record")
    assert record["available"] is False
    assert record["text"] == "The record does not say."
    assert d.transcript[-1]["kind"] == "record"


def test_an_empty_answer_from_the_court_is_not_recorded(tmp_path):
    events, emit = collect()
    d = _evidence_run(lambda *_a: {"granted": True, "record": ""}, tmp_path,
                      emit)
    assert "record" not in types_of(events)
    assert all(e.get("kind") != "record" for e in d.transcript)


def test_the_court_reports_what_an_experiment_shows(tmp_path):
    events, emit = collect()
    seen = {}

    def bailiff(kind, request, *_a):
        seen.update(kind=kind, request=request)
        return {"possible": True, "result": "The walk took 41 seconds."}
    foreman = scripted_foreman([
        {"action": "call_on", "target": 5},
        {"action": "rule_on_request", "seat": 5, "grant": True},
        HUNG])
    d = Deliberation(CASE, CARDS,
                     make_juror(action={"type": "propose_experiment",
                                        "description": "time the old man"}),
                     foreman, emit, turn_cap=200, transcript_dir=tmp_path,
                     run_id="test", bailiff_fn=bailiff)
    d.run()
    assert seen["kind"] == "experiment"
    assert seen["request"]["description"] == "time the old man"
    record = first(events, "record")
    assert record["kind"] == "experiment"
    assert record["available"] is True
    assert record["text"] == "The walk took 41 seconds."
    assert d.transcript[-1]["name"] == "the court"


def test_an_impossible_experiment_is_still_reported_back(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([
        {"action": "call_on", "target": 5},
        {"action": "rule_on_request", "seat": 5, "grant": True},
        HUNG])
    Deliberation(CASE, CARDS,
                 make_juror(action={"type": "propose_experiment",
                                    "description": "x"}),
                 foreman, emit, turn_cap=200, transcript_dir=tmp_path,
                 run_id="test",
                 bailiff_fn=lambda *_a: {"possible": False,
                                         "result": "The jury has no knife."}
                 ).run()
    record = first(events, "record")
    assert record["available"] is False
    assert record["text"] == "The jury has no knife."


def test_without_a_court_officer_the_jury_cannot_be_answered(tmp_path):
    events, emit = collect()
    d = _evidence_run(None, tmp_path, emit)
    assert "no court officer" in first(events, "error")["message"]
    assert "record" not in types_of(events)
    assert d.verdict == "hung"          # and the room carries on regardless


def test_a_failing_court_officer_does_not_stop_the_room(tmp_path):
    events, emit = collect()

    def broken(*_a):
        raise RuntimeError("bailiff died")
    d = _evidence_run(broken, tmp_path, emit)
    assert "record" not in types_of(events)
    assert d.verdict == "hung"


def test_a_court_officer_answering_off_schema_is_a_failure_not_a_crash(
        tmp_path):
    events, emit = collect()
    d = _evidence_run(lambda *_a: "the knife, I guess", tmp_path, emit)
    assert "record" not in types_of(events)
    assert d.verdict == "hung"


# --- the judge takes the verdict ------------------------------------------

def test_a_declared_verdict_is_announced_before_anyone_rules_on_it(tmp_path):
    events, emit = collect()
    d = make_delib(split_juror({1}),
                   scripted_foreman([{"action": "declare",
                                      "verdict": "not_guilty",
                                      "reason": "we all agree"}]),
                   emit, tmp_path)
    d.run()
    announced = first(events, "verdict_announced")
    assert announced["verdict"] == "not_guilty"
    assert announced["reason"] == "we all agree"
    assert types_of(events).index("verdict_announced") < \
        types_of(events).index("verdict")


def test_without_a_judge_the_foremans_word_stands(tmp_path):
    events, emit = collect()
    d = make_delib(split_juror({1}),
                   scripted_foreman([{"action": "declare", "verdict": "guilty",
                                      "reason": "done"}]),
                   emit, tmp_path)
    assert d.run() == "guilty"
    assert "judge_ruling" not in types_of(events)
    assert events[-1] == {"type": "verdict", "verdict": "guilty",
                          "reason": "done"}


def test_the_judge_accepts_the_verdict(tmp_path):
    events, emit = collect()
    seen = {}

    def judge(verdict, reason, last_tally, turn, transcript):
        seen.update(verdict=verdict, reason=reason, last_tally=last_tally,
                    turn=turn, transcript=list(transcript))
        return {"accept": True, "instruction": "So say you all."}
    d = make_delib(split_juror({1}),
                   scripted_foreman([{"action": "call_vote"},
                                     {"action": "declare",
                                      "verdict": "guilty", "reason": "done"}]),
                   emit, tmp_path, judge_fn=judge)
    assert d.run() == "guilty"
    assert seen["verdict"] == "guilty"
    assert seen["turn"] == 2
    assert seen["last_tally"]["guilty"] == 1
    ruling = first(events, "judge_ruling")
    assert ruling == {"type": "judge_ruling", "accepted": True,
                      "instruction": "So say you all."}
    assert events[-1]["type"] == "verdict"


def test_the_judge_sends_the_jury_back_and_it_keeps_deliberating(tmp_path):
    events, emit = collect()
    rulings = iter([
        {"accept": False, "instruction": "The count is not unanimous."},
        {"accept": True, "instruction": "So say you all."},
    ])
    foreman = scripted_foreman([
        {"action": "declare", "verdict": "guilty", "reason": "im tired"},
        {"action": "call_on", "target": 4},
        {"action": "declare", "verdict": "hung", "reason": "ok fine"},
    ])
    d = make_delib(split_juror({1, 2, 3}), foreman, emit, tmp_path,
                   judge_fn=lambda *_a: next(rulings))
    assert d.run() == "hung"
    refusal = first(events, "judge_ruling")
    assert refusal["accepted"] is False
    assert refusal["instruction"] == "The count is not unanimous."
    # the refusal is on the record the jurors read
    assert d.transcript[0] == {
        "kind": "record", "seat": None, "name": "the court",
        "speech": "The court refused the verdict. The count is not unanimous."}
    # and the foreman is told about it on his very next turn, once
    assert foreman.calls[1]["judge_note"] == "The count is not unanimous."
    assert foreman.calls[2]["judge_note"] is None
    assert d.verdict == "hung"


def test_a_refused_verdict_without_an_instruction_still_sends_them_back(
        tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([
        {"action": "declare", "verdict": "guilty", "reason": "x"},
        {"action": "call_on", "target": 4},
        {"action": "declare", "verdict": "hung", "reason": "y"}])
    accepts = iter([{"accept": False}, {"accept": True}])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path,
                   judge_fn=lambda *_a: next(accepts))
    assert d.run() == "hung"
    assert foreman.calls[1]["judge_note"] == "Continue deliberating."


def test_the_orchestrator_does_not_second_guess_a_non_unanimous_declare(
        tmp_path):
    """Whether a split room may return a verdict is the judge's call, not an
    `if` in this file."""
    events, emit = collect()
    d = make_delib(split_juror({1, 2, 3}),
                   scripted_foreman([{"action": "call_vote"},
                                     {"action": "declare",
                                      "verdict": "guilty",
                                      "reason": "close enough"}]),
                   emit, tmp_path, judge_fn=lambda *_a: {"accept": True,
                                                         "instruction": ""})
    assert d.run() == "guilty"
    assert first(events, "vote_result")["tally"]["guilty"] == 3


def test_a_failing_judge_leaves_the_case_open(tmp_path):
    events, emit = collect()
    calls = {"n": 0}

    def judge(*_a):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("no judge in chambers")
        return {"accept": True, "instruction": ""}
    foreman = scripted_foreman([{"action": "declare", "verdict": "hung",
                                 "reason": "x"}])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path, judge_fn=judge)
    assert d.run() == "hung"
    assert types_of(events).count("verdict_announced") == 2
    assert types_of(events).count("verdict") == 1


def test_the_judge_sees_the_traced_prompt_of_his_own_ruling(tmp_path):
    events, emit = collect()
    d = make_delib(split_juror({1}),
                   scripted_foreman([{"action": "declare", "verdict": "hung",
                                      "reason": "x"}]),
                   emit, tmp_path,
                   judge_fn=lambda *_a: {"accept": True, "instruction": "ok",
                                         "_prompt": {"system": "s",
                                                     "user": "u"},
                                         "_raw_output": "{}"})
    d.run()
    assert first(events, "prompt")["seat"] == "judge"


@pytest.mark.parametrize("verdict", ["mistrial", "", None, "not guilty"])
def test_a_verdict_the_law_does_not_know_is_unusable(verdict, tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([{"action": "declare", "verdict": verdict,
                                 "reason": "x"}, HUNG])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path)
    assert d.run() == "hung"
    assert "unusable action" in first(events, "error")["message"]


# --- guards ----------------------------------------------------------------

def test_the_turn_cap_forces_a_hung_jury(tmp_path):
    events, emit = collect()
    foreman = scripted_foreman([{"action": "call_on", "target": 8}])
    d = make_delib(split_juror({1}), foreman, emit, tmp_path, cap=5)
    assert d.run() == "hung"
    assert events[-1]["reason"] == "turn cap reached"
    assert len(only(events, "speech")) == 5


def test_a_dead_backend_aborts_instead_of_grinding(tmp_path):
    def dead(*_a, **_kw):
        raise ConnectionError("refused")
    events, emit = collect()
    d = make_delib(dead, dead, emit, tmp_path)
    assert d.run() == "aborted"
    assert d.turn <= FAIL_CAP + 1          # no 200-turn pass grind
    assert events[-1]["type"] == "error"
    assert "consecutive" in events[-1]["message"]


def test_the_failure_counter_resets_on_success(tmp_path):
    calls = {"n": 0}

    def flaky(_card, _case, _transcript, mode, *_a):
        if mode == "vote":
            return {"vote": "undecided"}
        calls["n"] += 1
        if calls["n"] % 2:                 # alternate fail/succeed
            raise ConnectionError("refused")
        return {"speech": "ok", "lean": "undecided", "confidence": 0.5}
    events, emit = collect()
    foreman = scripted_foreman([{"action": "call_on", "target": s}
                                for s in range(1, 13)])
    d = make_delib(flaky, foreman, emit, tmp_path, cap=12)
    assert d.run() == "hung"               # turn cap, never aborted


# --- the operator's stop ---------------------------------------------------

def test_should_stop_ends_the_run_before_anything_happens(tmp_path):
    events, emit = collect()
    d = make_delib(split_juror({1, 2, 3}), scripted_foreman([HUNG]), emit,
                   tmp_path, should_stop=lambda: True,
                   assess_fn=make_assessor())
    assert d.run() == "stopped"
    assert events[-1] == {"type": "verdict", "verdict": "stopped",
                          "reason": "stopped by operator"}
    assert only(events, "speech") == []
    assert only(events, "assessed") == []


def test_check_stop_raises_the_unwinding_exception(tmp_path):
    events, emit = collect()
    d = make_delib(split_juror({1}), scripted_foreman([HUNG]), emit, tmp_path,
                   should_stop=lambda: True)
    with pytest.raises(Stopped):
        d._check_stop()


def test_a_stop_during_the_assessment_lands_before_the_room_is_told(tmp_path):
    """Twelve concurrent reads are the longest single step in a run; the stop
    has to land at the end of it, not after the whole exhibit."""
    stop = threading.Event()
    done = []

    def assessor(card, *_a):
        done.append(card["seat"])
        if len(done) == 12:
            stop.set()
        return {"position": "raises_doubt", "reasoning": "r"}
    events, emit = collect()
    d = make_delib(split_juror({1}), scripted_foreman([HUNG]), emit, tmp_path,
                   should_stop=stop.is_set, assess_fn=assessor)
    assert d.run() == "stopped"
    assert len(only(events, "assessed")) == 12
    assert "positions" not in types_of(events)      # never reached the room
    assert only(events, "speech") == []
    assert events[-1]["verdict"] == "stopped"


def test_a_stop_mid_turn_lands_before_the_foremans_action_is_carried_out(
        tmp_path):
    stop = threading.Event()

    def foreman(*_a, **_kw):
        stop.set()                      # the operator hits stop mid-call
        return {"action": "call_on", "target": 8}
    events, emit = collect()
    d = make_delib(split_juror({1}), foreman, emit, tmp_path,
                   should_stop=stop.is_set)
    assert d.run() == "stopped"
    assert "speaker" not in types_of(events)
    assert only(events, "speech") == []


def test_a_stop_during_a_ballot_lands_before_the_count_is_read_out(tmp_path):
    stop = threading.Event()
    cast = []

    def juror(card, _case, _transcript, mode, *_a):
        if mode != "vote":
            return {"speech": "x", "lean": "guilty", "confidence": 0.5}
        cast.append(card["seat"])
        if len(cast) == 12:
            stop.set()
        return {"vote": "guilty"}
    events, emit = collect()
    foreman = scripted_foreman([{"action": "call_vote"}, HUNG])
    d = make_delib(juror, foreman, emit, tmp_path, should_stop=stop.is_set)
    assert d.run() == "stopped"
    assert len(only(events, "voter_done")) == 12
    assert "vote_result" not in types_of(events)
    assert d.last_tally is None
    assert events[-1]["verdict"] == "stopped"


def test_a_stop_lands_before_the_court_officer_is_sent_for(tmp_path):
    stop = threading.Event()
    asked = []

    def juror(_card, _case, _transcript, mode, *_a):
        if mode != "speak":
            return {"vote": "guilty"}
        stop.set()
        return {"speech": "send for the plan", "lean": "guilty",
                "confidence": 0.5,
                "action": {"type": "request_evidence", "item": "the plan"}}
    events, emit = collect()
    foreman = scripted_foreman([
        {"action": "call_on", "target": 8},
        {"action": "rule_on_request", "seat": 8, "grant": True}, HUNG])
    d = make_delib(juror, foreman, emit, tmp_path, should_stop=stop.is_set,
                   bailiff_fn=lambda *_a: asked.append(1))
    assert d.run() == "stopped"
    assert asked == []


def test_a_stop_lands_before_the_judge_is_troubled(tmp_path):
    stop = threading.Event()
    ruled = []

    def foreman(*_a, **_kw):
        stop.set()
        return {"action": "declare", "verdict": "hung", "reason": "x"}
    events, emit = collect()
    d = make_delib(split_juror({1}), foreman, emit, tmp_path,
                   should_stop=stop.is_set,
                   judge_fn=lambda *_a: ruled.append(1))
    assert d.run() == "stopped"
    assert ruled == []
    assert "verdict_announced" not in types_of(events)


# --- the written record ----------------------------------------------------

def test_write_transcript_dumps_all_events(tmp_path):
    events, emit = collect()
    d = make_delib(make_juror(), scripted_foreman([HUNG]), emit, tmp_path)
    d._call_on(1)
    path = d._write_transcript()
    assert json.loads(path.read_text()) == events
    assert path.name == "test.json"


def test_a_stopped_run_still_writes_its_transcript(tmp_path):
    events, emit = collect()
    d = make_delib(split_juror({1}), scripted_foreman([HUNG]), emit, tmp_path,
                   should_stop=lambda: True)
    d.run()
    saved = json.loads((tmp_path / "test.json").read_text())
    assert saved == events
    assert saved[-1]["verdict"] == "stopped"


def test_transcript_written_without_audio(tmp_path):
    def audible(_card, _case, _transcript, mode, *_a):
        if mode == "vote":
            return {"vote": "guilty"}
        return {"speech": "hi", "lean": "guilty", "confidence": 0.5,
                "audio": "data:audio/mpeg;base64,HUGE"}
    events, emit = collect()
    foreman = scripted_foreman([{"action": "call_on", "target": 1}])
    d = make_delib(audible, foreman, emit, tmp_path, cap=1)
    d.run()
    saved = json.loads((tmp_path / "test.json").read_text())
    assert any(e["type"] == "speech" for e in saved)
    assert all("audio" not in e for e in saved)
    # the live event still carried it
    assert "audio" in first(events, "speech")
