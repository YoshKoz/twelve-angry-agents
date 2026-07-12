"""Deliberation state machine. Sequences agent calls, owns all state,
emits events. Contains NO LLM logic of its own — agents are injected
callables, so tests drive it with deterministic fakes."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tally import tally, unanimous, VOTE_VALUES

TURN_CAP = 200


class Deliberation:
    def __init__(self, case_text, cards, juror_fn, foreman_fn, emit,
                 turn_cap=TURN_CAP, transcript_dir="transcripts",
                 run_id=None):
        self.case_text = case_text
        self.cards = {c["seat"]: c for c in cards}
        self.juror_fn = juror_fn
        self.foreman_fn = foreman_fn
        self._emit = emit
        self.turn_cap = turn_cap
        self.transcript_dir = Path(transcript_dir)
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
        self.transcript = []          # spoken record: {seat, name, speech}
        self.events = []              # every emitted event, in order
        self.leans = {}               # seat -> {lean, confidence}  PRIVATE
        self.last_tally = None
        self.turn = 0
        self.spoke_since_vote = True  # True so the forced opening vote passes
        self.verdict = None
        self._rr_next = 1             # round-robin fallback pointer

    def emit(self, event):
        self.events.append(event)
        self._emit(event)

    # --- main loop ---------------------------------------------------------
    def run(self):
        """Run the deliberation to a verdict. Returns the verdict string."""
        self.emit({"type": "case", "text": self.case_text})
        self.emit({"type": "roster", "jurors": [
            {"seat": s, "name": c["name"], "occupation": c["occupation"]}
            for s, c in sorted(self.cards.items())]})
        self._call_vote()                    # forced opening ballot
        while self.verdict is None:
            self.turn += 1
            if self.turn > self.turn_cap:
                self._declare("hung", "turn cap reached")
                break
            self._foreman_turn()
        self._write_transcript()
        return self.verdict

    def _foreman_turn(self):
        try:
            action = self.foreman_fn(self.transcript, self.last_tally,
                                     self.turn, self.turn_cap)
        except Exception:
            self._call_on(self._round_robin_seat())
            return
        kind = action.get("action")
        if kind == "call_on" and action.get("target") in self.cards:
            self._call_on(action["target"])
        elif kind == "call_vote":
            if self.spoke_since_vote:
                self._call_vote()
            else:                      # no back-to-back ballots
                self._call_on(self._round_robin_seat())
        elif (kind == "declare"
              and action.get("verdict") in ("guilty", "not_guilty", "hung")):
            counts = self.last_tally or {}
            if (action["verdict"] != "hung"
                    and counts.get(action["verdict"], 0) != 12):
                # premature declare without a unanimous ballot: rejected
                self._call_on(self._round_robin_seat())
            else:
                self._declare(action["verdict"], action.get("reason", ""))
        else:                          # unknown/invalid action
            self._call_on(self._round_robin_seat())

    def _round_robin_seat(self):
        seat = self._rr_next
        self._rr_next = seat % 12 + 1
        return seat

    # --- juror speaks ----------------------------------------------------
    def _call_on(self, seat):
        card = self.cards[seat]
        self.emit({"type": "speaker", "seat": seat})
        try:
            reply = self.juror_fn(card, self.case_text, self.transcript,
                                  "speak")
        except Exception:
            self.emit({"type": "speech", "seat": seat, "name": card["name"],
                       "speech": f"(Juror #{seat} passes.)"})
            return
        self.transcript.append({"seat": seat, "name": card["name"],
                                "speech": reply["speech"]})
        self.leans[seat] = {"lean": reply.get("lean", "undecided"),
                            "confidence": reply.get("confidence", 0)}
        self.spoke_since_vote = True
        self.emit({"type": "speech", "seat": seat, "name": card["name"],
                   "speech": reply["speech"]})

    # --- public ballot ---------------------------------------------------
    def _call_vote(self):
        self.emit({"type": "vote_called"})
        seats = sorted(self.cards)

        def one_vote(seat):
            try:
                r = self.juror_fn(self.cards[seat], self.case_text,
                                  self.transcript, "vote")
                v = r.get("vote")
                return v if v in VOTE_VALUES else "undecided"
            except Exception:
                return "undecided"

        with ThreadPoolExecutor(max_workers=12) as pool:
            votes = dict(zip(seats, pool.map(one_vote, seats)))
        counts = tally(votes)
        self.last_tally = counts
        self.spoke_since_vote = False
        self.emit({"type": "vote_result", "votes": votes, "tally": counts})
        result = unanimous(counts)
        if result:
            self._declare(result, "unanimous vote")

    # --- end of deliberation ----------------------------------------------
    def _declare(self, verdict, reason):
        self.verdict = verdict
        self.emit({"type": "verdict", "verdict": verdict, "reason": reason})

    def _write_transcript(self):
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        path = self.transcript_dir / f"{self.run_id}.json"
        path.write_text(json.dumps(self.events, indent=2))
        return path
