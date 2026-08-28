"""Deliberation state machine. Sequences agent calls, owns all state,
emits events. Contains NO LLM logic of its own — agents are injected
callables, so tests drive it with deterministic fakes.

The room works the docket: one exhibit at a time. Each exhibit opens with all
twelve jurors reading it independently and concurrently — nobody has spoken,
nobody is watching, and that is the honest picture of where the room stands.
Then they argue about it, and the foreman closes it when the argument stops
producing anything new.

Every judgment belongs to an agent. The foreman picks speakers, chooses when
and how the room votes, rules on requests, and decides when an exhibit is
done. Jurors demand ballots, send for exhibits, propose experiments, abstain,
and change their votes on the floor. The court officer answers from the
record. The judge takes the verdict or sends the jury back.

What remains in code is only what is nobody's decision: bookkeeping, error
handling when an agent fails or answers with the wrong shape, a runaway turn
cap, and the operator's stop.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tally import tally, VOTE_VALUES

TURN_CAP = 200
FAIL_CAP = 5      # consecutive agent failures before aborting the run
EXHIBIT_TURN_CAP = 8    # a runaway guard per exhibit, not a pacing rule

JUROR_ACTIONS = ("none", "demand_vote", "request_evidence",
                 "propose_experiment", "change_vote", "challenge")
VOTE_METHODS = ("hands", "secret")
POSITIONS = ("supports_guilt", "raises_doubt", "inconclusive")


class Stopped(Exception):
    """The operator stopped the run. Unwinds whatever the room was doing."""


def _request_summary(action):
    """One line describing an open request, for the foreman's prompt."""
    kind = action.get("type")
    if kind == "demand_vote":
        return f"demands a vote by {action.get('method', 'hands')}"
    if kind == "request_evidence":
        return f"sends out for: {action.get('item', '')}"
    if kind == "propose_experiment":
        return f"proposes the room test: {action.get('description', '')}"
    if kind == "challenge":
        return f"puts a direct question to Juror #{action.get('target')}"
    return f"has a request ({kind})"


def position_summary(positions):
    """How a room came out on one exhibit, in a phrase — this is what later
    prompts carry instead of the whole argument."""
    counts = {p: 0 for p in POSITIONS}
    for p in positions.values():
        if p in counts:
            counts[p] += 1
    return (f"{counts['supports_guilt']} for the prosecution, "
            f"{counts['raises_doubt']} doubting it, "
            f"{counts['inconclusive']} unmoved")


class Deliberation:
    def __init__(self, case, cards, juror_fn, foreman_fn, emit,
                 turn_cap=TURN_CAP, transcript_dir="transcripts",
                 run_id=None, should_stop=None, bailiff_fn=None,
                 judge_fn=None, assess_fn=None):
        self.case = case
        self.cards = {c["seat"]: c for c in cards}
        self.juror_fn = juror_fn
        self.foreman_fn = foreman_fn
        # Optional only so a test can drive the room without them. In a live
        # run all three are real agents.
        self.bailiff_fn = bailiff_fn
        self.judge_fn = judge_fn
        self.assess_fn = assess_fn
        self._emit = emit
        self.turn_cap = turn_cap
        self.should_stop = should_stop or (lambda: False)
        self.transcript_dir = Path(transcript_dir)
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
        self.transcript = []          # spoken record + what the court read in
        self.events = []              # every emitted event, in order
        self.leans = {}               # seat -> {lean, confidence}  PRIVATE
        self.last_votes = {}          # seat -> vote, from the last ballot
        self.last_vote_reasons = {}
        self.last_tally = None
        self.last_method = None
        self.turn = 0
        self.verdict = None
        self.pending = {}             # seat -> open request the foreman owes
        # pre-seeded with every seat, so this doubles as the roster the
        # foreman is told he may call on
        self.speech_counts = {s: 0 for s in self.cards}
        self.floor_notes = {}
        self.judge_note = None
        self.docket = list(case.get("exhibits", []))
        self.exhibit_idx = 0          # which exhibit is in front of the room
        self.exhibit_turns = 0
        self.positions = {}           # exhibit id -> {seat: position}
        self.findings = []            # closed exhibits, in order
        self._speak_idx = 0
        self._last_spoke = {}
        self._fails = {}   # role -> consecutive failures

    # --- plumbing ----------------------------------------------------------
    def emit(self, event):
        self.events.append(event)
        self._emit(event)

    def _check_stop(self):
        """Checked before and after every agent call, so Stop lands within one
        call rather than one turn — a turn can be several agent calls long."""
        if self.should_stop():
            raise Stopped()

    @property
    def exhibit(self):
        return (self.docket[self.exhibit_idx]
                if self.exhibit_idx < len(self.docket) else None)

    def _findings_for_prompt(self):
        return [{"name": f["name"], "summary": f["summary"]}
                for f in self.findings]

    # --- main loop ---------------------------------------------------------
    def run(self):
        """Run the deliberation to a verdict. Returns the verdict string."""
        self.emit({"type": "case", "text": self.case["narrative"],
                   "title": self.case.get("title", ""),
                   "charge": self.case.get("charge", "")})
        self.emit({"type": "roster", "jurors": [
            {"seat": s, "name": c["name"], "occupation": c["occupation"],
             "emoji": c.get("emoji", "")}
            for s, c in sorted(self.cards.items())]})
        self.emit({"type": "docket", "exhibits": [
            {"id": e["id"], "name": e["name"],
             "claim": e["prosecution_claim"]} for e in self.docket]})
        try:
            self._open_exhibit()
            while self.verdict is None:
                self._check_stop()
                self.turn += 1
                if self.turn > self.turn_cap:
                    self._declare("hung", "turn cap reached")
                    break
                self._foreman_turn()
        except Stopped:
            self._declare("stopped", "stopped by operator")
        self._write_transcript()
        return self.verdict

    def _role_of(self, who):
        """Which agent role a failure belongs to. Counted per role because a
        single shared counter let a live foreman mask a dead one: every
        foreman turn reset it, so a court officer or an assessor that failed
        every single time never reached FAIL_CAP and the run limped on
        forever."""
        if who.startswith("juror"):
            return "juror"
        return who or "agent"

    def _agent_succeeded(self, who=""):
        self._fails.pop(self._role_of(who), None)

    def _agent_failed(self, who=""):
        role = self._role_of(who)
        self._fails[role] = self._fails.get(role, 0) + 1
        if self._fails[role] >= FAIL_CAP and self.verdict is None:
            self.verdict = "aborted"
            self.emit({"type": "error", "message":
                       f"{self._fails[role]} consecutive {role} failures "
                       f"{'(' + who + ') ' if who else ''}"
                       "— is the LLM backend running?"})

    # --- the docket --------------------------------------------------------
    def _open_exhibit(self):
        """Put an exhibit in front of the room and take everyone's independent
        read of it before a word is spoken. All twelve run concurrently."""
        exhibit = self.exhibit
        if exhibit is None:
            return
        self.exhibit_turns = 0
        self.emit({"type": "exhibit", "id": exhibit["id"],
                   "name": exhibit["name"],
                   "claim": exhibit["prosecution_claim"],
                   "record": exhibit["record"],
                   "index": self.exhibit_idx, "total": len(self.docket)})
        if self.assess_fn is None:
            return
        self._check_stop()

        def one(seat):
            try:
                return seat, self.assess_fn(self.cards[seat], self.case,
                                            exhibit,
                                            self._findings_for_prompt())
            except Exception:
                return seat, None

        results = {}
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(one, s) for s in sorted(self.cards)]
            for future in as_completed(futures):
                seat, reply = future.result()
                results[seat] = reply
                self.emit({"type": "assessed", "seat": seat,
                           "exhibit": exhibit["id"]})
        self._check_stop()

        positions, reasons = {}, {}
        failures = 0
        for seat in sorted(self.cards):
            reply = results.get(seat)
            if not isinstance(reply, dict):
                failures += 1
                positions[seat] = "inconclusive"
                reasons[seat] = ""
                continue
            self._emit_trace(seat, reply)
            pos = reply.get("position")
            positions[seat] = pos if pos in POSITIONS else "inconclusive"
            reasons[seat] = reply.get("reasoning", "")
        if failures:
            self._agent_failed("assessment")
        else:
            self._agent_succeeded("assessment")
        self.positions[exhibit["id"]] = positions
        self.emit({"type": "positions", "exhibit": exhibit["id"],
                   "positions": positions, "reasons": reasons,
                   "summary": position_summary(positions)})

    def _close_exhibit(self, finding):
        exhibit = self.exhibit
        if exhibit is None:
            return
        positions = self.positions.get(exhibit["id"], {})
        summary = finding or position_summary(positions)
        self.findings.append({"id": exhibit["id"], "name": exhibit["name"],
                              "summary": summary})
        self.emit({"type": "exhibit_closed", "id": exhibit["id"],
                   "name": exhibit["name"], "finding": summary})
        self.exhibit_idx += 1
        if self.exhibit is not None:
            self._open_exhibit()
        else:
            self.emit({"type": "docket_closed",
                       "findings": list(self.findings)})

    # --- foreman -----------------------------------------------------------
    def _foreman_turn(self):
        pending = [{"seat": s, "summary": _request_summary(a)}
                   for s, a in sorted(self.pending.items())]
        exhibit = self.exhibit
        try:
            action = self.foreman_fn(
                self.case, self.transcript, self.last_tally, self.turn,
                self.turn_cap, pending, dict(self.speech_counts),
                self.judge_note, exhibit, self._findings_for_prompt(),
                self.exhibit_turns, max(0, len(self.docket) - self.exhibit_idx - 1))
        except Stopped:
            raise
        except Exception:
            self._agent_failed("foreman")
            if self.verdict is None:
                self._call_on(self._fallback_speaker())
            return
        self._check_stop()
        self._emit_trace("foreman", action)
        if not isinstance(action, dict):
            self._agent_failed("foreman")
            if self.verdict is None:
                self._call_on(self._fallback_speaker())
            return
        self._agent_succeeded("foreman")
        self.judge_note = None        # delivered
        kind = action.get("action")

        if kind == "call_on" and action.get("target") in self.cards:
            self._call_on(action["target"])
        elif kind == "call_on":
            self.emit({"type": "error", "message":
                       f"foreman called on #{action.get('target')!r}, "
                       "who is not seated on this jury"})
            self._call_on(self._fallback_speaker())
        elif kind == "close_exhibit":
            if exhibit is None:
                self._call_on(self._fallback_speaker())
            else:
                self._close_exhibit(action.get("finding", ""))
        elif kind == "call_vote":
            method = action.get("method")
            self._call_vote(method if method in VOTE_METHODS else "hands",
                            binding=bool(action.get("binding", True)))
        elif kind == "rule_on_request":
            self._rule_on_request(action)
        elif (kind == "declare"
              and action.get("verdict") in ("guilty", "not_guilty", "hung")):
            self._seek_verdict(action["verdict"], action.get("reason", ""))
        else:
            self.emit({"type": "error", "message":
                       f"foreman returned an unusable action: {kind!r}"})
            self._call_on(self._fallback_speaker())

        # Every turn spent while this exhibit was open counts against it, not
        # only the ones that put a juror on the floor — a foreman who loops on
        # ballots and rulings is just as stuck, and used to slip past the cap
        # entirely because nobody was speaking. Counted after the fact, so the
        # foreman is told the turns already spent, not the one he is taking.
        if exhibit is not None and self.exhibit is exhibit:
            self.exhibit_turns += 1

        # A room that argues one exhibit forever never reaches the verdict.
        # This is a runaway guard, not a pacing rule: the foreman is expected
        # to close exhibits himself, long before this bites.
        if (self.exhibit is not None and self.verdict is None
                and self.exhibit_turns >= EXHIBIT_TURN_CAP):
            self.emit({"type": "error", "message":
                       f"exhibit ran past {EXHIBIT_TURN_CAP} turns; "
                       "the court moves the room on"})
            self._close_exhibit("")

    def _fallback_speaker(self):
        """Who speaks when the foreman could not say. Not a policy — this runs
        only after an agent failure or an unparseable action."""
        return min(sorted(self.cards),
                   key=lambda s: self._last_spoke.get(s, 0))

    def _rule_on_request(self, action):
        seat = action.get("seat")
        request = self.pending.pop(seat, None)
        if request is None:
            self.emit({"type": "error", "message":
                       f"foreman ruled on a request from #{seat} "
                       "that was not open"})
            self._call_on(self._fallback_speaker())
            return
        granted = bool(action.get("grant"))
        self.emit({"type": "ruling", "seat": seat, "granted": granted,
                   "reason": action.get("reason", ""),
                   "request": _request_summary(request)})
        if not granted:
            return
        kind = request.get("type")
        if kind == "demand_vote":
            method = request.get("method")
            self._call_vote(method if method in VOTE_METHODS else "hands",
                            binding=True)
        elif kind == "request_evidence":
            self._court_answers("evidence", request, seat)
        elif kind == "propose_experiment":
            self._court_answers("experiment", request, seat)
        elif kind == "challenge" and request.get("target") in self.cards:
            target = request["target"]
            self.floor_notes[target] = (
                f"Juror #{seat} has put a direct question to you and the "
                "foreman has told you to answer it. Address them directly.")
            self._call_on(target)

    # --- the court officer -------------------------------------------------
    def _court_answers(self, kind, request, seat):
        if self.bailiff_fn is None:
            self.emit({"type": "error", "message":
                       "no court officer available to answer the jury"})
            return
        self._check_stop()
        try:
            reply = self.bailiff_fn(kind, request, seat, self.case,
                                    self.transcript)
        except Stopped:
            raise
        except Exception:
            self._agent_failed("bailiff")
            return
        if not isinstance(reply, dict):
            self._agent_failed("bailiff")
            return
        self._agent_succeeded("bailiff")
        if kind == "evidence":
            ok, text = bool(reply.get("granted")), reply.get("record", "")
        else:
            ok, text = bool(reply.get("possible")), reply.get("result", "")
        if not text:
            return
        self.transcript.append({"kind": "record", "seat": None,
                                "name": "the court", "speech": text})
        self.emit({"type": "record", "kind": kind, "seat": seat,
                   "available": ok, "text": text})

    # --- juror speaks ------------------------------------------------------
    def _emit_trace(self, seat, reply):
        if not isinstance(reply, dict):
            return
        if "_prompt" in reply:
            self.emit({"type": "prompt", "seat": seat,
                       "system": reply["_prompt"]["system"],
                       "user": reply["_prompt"]["user"]})
        if "_raw_output" in reply:
            mode = ("speak" if "speech" in reply
                    else "vote" if "vote" in reply
                    else "assess" if "position" in reply else "decide")
            self.emit({"type": "reasoning", "seat": seat,
                       "raw": reply["_raw_output"], "mode": mode})

    def _call_on(self, seat):
        self._speak_idx += 1
        self._last_spoke[seat] = self._speak_idx
        self.speech_counts[seat] = self.speech_counts.get(seat, 0) + 1
        # exhibit_turns is counted once per foreman turn, not here — a turn
        # that calls on a juror and then answers a challenge is still one turn
        card = self.cards[seat]
        note = self.floor_notes.pop(seat, None)
        self.emit({"type": "speaker", "seat": seat})
        self._check_stop()
        try:
            reply = self.juror_fn(card, self.case, self.transcript, "speak",
                                  self.last_tally, note, None, self.exhibit,
                                  self._findings_for_prompt())
        except Stopped:
            raise
        except Exception:
            self._agent_failed(f"juror #{seat}")
            if self.verdict is None:
                self.emit({"type": "speech", "seat": seat,
                           "name": card["name"],
                           "speech": f"(Juror #{seat} passes.)"})
            return
        self._emit_trace(seat, reply)
        # An agent that raises is handled above; one that answers with the
        # wrong shape has failed just as completely.
        if not isinstance(reply, dict) or not reply.get("speech"):
            self._agent_failed(f"juror #{seat}")
            if self.verdict is None:
                self.emit({"type": "speech", "seat": seat,
                           "name": card["name"],
                           "speech": f"(Juror #{seat} passes.)"})
            return
        self._agent_succeeded(f"juror #{seat}")
        self.transcript.append({"seat": seat, "name": card["name"],
                                "speech": reply["speech"]})
        lean = reply.get("lean", "undecided")
        self.leans[seat] = {"lean": lean,
                            "confidence": reply.get("confidence", 0)}
        event = {"type": "speech", "seat": seat, "name": card["name"],
                 "speech": reply["speech"]}
        # Not the lean itself — just whether it now conflicts with the last
        # public ballot, so the room can hint a mind may be changing.
        last_vote = self.last_votes.get(seat)
        event["reconsidering"] = bool(
            last_vote and lean != "undecided" and lean != last_vote)
        if "audio" in reply:
            event["audio"] = reply["audio"]
        self.emit(event)
        self._handle_juror_action(seat, reply.get("action"))

    def _handle_juror_action(self, seat, action):
        if not isinstance(action, dict):
            return
        kind = action.get("type")
        if kind in (None, "none") or kind not in JUROR_ACTIONS:
            return
        if kind == "change_vote":
            self._change_vote(seat, action.get("vote"))
            return
        self.pending[seat] = action
        self.emit({"type": "request", "seat": seat, "kind": kind,
                   "summary": _request_summary(action)})

    def _change_vote(self, seat, vote):
        """A juror switching sides out loud. It stands on its own — no one
        grants it — and it re-counts the room immediately."""
        if vote not in ("guilty", "not_guilty") or not self.last_votes:
            return
        if self.last_votes.get(seat) == vote:
            return
        self.last_votes[seat] = vote
        self.last_tally = tally(self.last_votes)
        self.emit({"type": "vote_change", "seat": seat, "vote": vote,
                   "tally": self.last_tally})

    # --- public ballot -----------------------------------------------------
    def _call_vote(self, method="hands", binding=True):
        self._check_stop()
        self.emit({"type": "vote_called", "binding": binding,
                   "method": method})
        seats = sorted(self.cards)

        def one_vote(seat):
            try:
                r = self.juror_fn(self.cards[seat], self.case,
                                  self.transcript, "vote", self.last_tally,
                                  None, method, self.exhibit,
                                  self._findings_for_prompt())
                v = r.get("vote")
                if v == "abstain" and method != "secret":
                    v = "undecided"          # no hiding in a show of hands
                return (v if v in VOTE_VALUES else "undecided", r)
            except Exception:
                return ("undecided", {})

        results = {}
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = {pool.submit(one_vote, seat): seat for seat in seats}
            for future in as_completed(futures):
                seat = futures[future]
                results[seat] = future.result()
                # no vote value revealed here — just that the ballot is in
                self.emit({"type": "voter_done", "seat": seat})
        self._check_stop()
        votes, reasons = {}, {}
        for seat in seats:
            v, reply = results[seat]
            votes[seat] = v
            reasons[seat] = reply.get("reasoning", "") if reply else ""
            self._emit_trace(seat, reply)
        counts = tally(votes)
        self.last_tally = counts
        self.last_votes = votes
        self.last_vote_reasons = reasons
        self.last_method = method
        # A secret ballot is secret: the room learns the count, not who cast
        # what. The trace panel still gets each juror's reasoning, since that
        # is the operator's view of the machinery, not the jury's.
        self.emit({"type": "vote_result",
                   "votes": {} if method == "secret" else votes,
                   "tally": counts, "reasons": reasons, "binding": binding,
                   "method": method, "secret": method == "secret"})
        # A unanimous ballot does not end the case by itself — the foreman
        # still has to take it to the judge.

    # --- end of deliberation -----------------------------------------------
    def _seek_verdict(self, verdict, reason):
        """The foreman announces a finding; the judge decides if it stands."""
        self.emit({"type": "verdict_announced", "verdict": verdict,
                   "reason": reason})
        if self.judge_fn is None:
            self._declare(verdict, reason)
            return
        self._check_stop()
        try:
            ruling = self.judge_fn(verdict, reason, self.last_tally,
                                   self.turn, self.transcript)
        except Stopped:
            raise
        except Exception:
            self._agent_failed("judge")
            return
        self._emit_trace("judge", ruling)
        if not isinstance(ruling, dict):
            self._agent_failed("judge")
            return
        self._agent_succeeded("judge")
        instruction = ruling.get("instruction", "")
        if ruling.get("accept"):
            self.emit({"type": "judge_ruling", "accepted": True,
                       "instruction": instruction})
            self._declare(verdict, reason)
        else:
            self.emit({"type": "judge_ruling", "accepted": False,
                       "instruction": instruction})
            self.judge_note = instruction or "Continue deliberating."
            self.transcript.append({
                "kind": "record", "seat": None, "name": "the court",
                "speech": f"The court refused the verdict. {instruction}"})

    def _declare(self, verdict, reason):
        self.verdict = verdict
        self.emit({"type": "verdict", "verdict": verdict, "reason": reason})

    def _write_transcript(self):
        # audio is regenerable from the TTS cache; embedding it here would
        # make every transcript file tens of MB of base64 MP3
        lean_events = [{k: v for k, v in e.items() if k != "audio"}
                       for e in self.events]
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        path = self.transcript_dir / f"{self.run_id}.json"
        path.write_text(json.dumps(lean_events, indent=2))
        return path
