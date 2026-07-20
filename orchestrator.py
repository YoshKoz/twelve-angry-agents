"""Deliberation state machine. Sequences agent calls, owns all state,
emits events. Contains NO LLM logic of its own — agents are injected
callables, so tests drive it with deterministic fakes."""

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tally import tally, unanimous, VOTE_VALUES

TURN_CAP = 200
FAIL_CAP = 5      # consecutive agent failures before aborting the run
FORCE_VOTE_EVERY = 15   # discussion turns before the room re-votes even if
                        # the foreman never chooses to call one itself
SPEAKER_COOLDOWN = 3    # a juror can't be called on again until this many
                        # other speaking turns have passed — stops the foreman
                        # fixating on one seat and the room echoing itself


class Deliberation:
    def __init__(self, case_text, cards, juror_fn, foreman_fn, emit,
                 turn_cap=TURN_CAP, transcript_dir="transcripts",
                 run_id=None, should_stop=None):
        self.case_text = case_text
        self.cards = {c["seat"]: c for c in cards}
        self.juror_fn = juror_fn
        self.foreman_fn = foreman_fn
        self._emit = emit
        self.turn_cap = turn_cap
        # checked once per turn; lets an operator cancel a run in progress
        self.should_stop = should_stop or (lambda: False)
        self.transcript_dir = Path(transcript_dir)
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
        self.transcript = []          # spoken record: {seat, name, speech}
        self.events = []              # every emitted event, in order
        self.leans = {}               # seat -> {lean, confidence}  PRIVATE
        self.last_votes = {}          # seat -> vote string, from last public ballot
        self.last_vote_reasons = {}   # seat -> private reasoning from last ballot
        self.last_tally = None
        self.turn = 0
        self.spoke_since_vote = True  # True so the forced opening vote passes
        self.verdict = None
        self._speak_idx = 0           # monotonic counter of speaking turns
        self._last_spoke = {}         # seat -> _speak_idx of its last speech
        self._consec_failures = 0     # abort when this hits FAIL_CAP
        self._turns_since_vote = 0    # forces a re-vote at FORCE_VOTE_EVERY

    def emit(self, event):
        self.events.append(event)
        self._emit(event)

    # --- main loop ---------------------------------------------------------
    def run(self):
        """Run the deliberation to a verdict. Returns the verdict string."""
        self.emit({"type": "case", "text": self.case_text})
        self.emit({"type": "roster", "jurors": [
            {"seat": s, "name": c["name"], "occupation": c["occupation"],
             "emoji": c.get("emoji", "")}
            for s, c in sorted(self.cards.items())]})
        # Opening ballot is a non-binding straw poll: it surfaces the split
        # and forces discussion, but cannot end the case — no jury convicts in
        # this room without deliberating first, even on a unanimous first show
        # of hands.
        self._call_vote(binding=False)
        while self.verdict is None:
            if self.should_stop():
                self._declare("stopped", "stopped by operator")
                break
            self.turn += 1
            if self.turn > self.turn_cap:
                self._declare("hung", "turn cap reached")
                break
            if (self._turns_since_vote >= FORCE_VOTE_EVERY
                    and self.spoke_since_vote):
                self._call_vote()
            else:
                self._foreman_turn()
        self._write_transcript()
        return self.verdict

    def _agent_failed(self):
        """Count a failed agent call; abort the run on FAIL_CAP in a row."""
        self._consec_failures += 1
        if self._consec_failures >= FAIL_CAP and self.verdict is None:
            self.verdict = "aborted"
            self.emit({"type": "error", "message":
                       f"{self._consec_failures} consecutive agent failures "
                       "— is the LLM backend running?"})

    def _foreman_turn(self):
        try:
            action = self.foreman_fn(self.transcript, self.last_tally,
                                     self.turn, self.turn_cap)
        except Exception:
            self._agent_failed()
            if self.verdict is None:
                self._call_on(self._pick_speaker())
            return
        self._consec_failures = 0
        kind = action.get("action")
        if kind == "call_on" and action.get("target") in self.cards:
            # honor the foreman's pick unless that juror just spoke — then
            # spread the turn so no one seat monopolizes the room
            self._call_on(self._pick_speaker(action["target"]))
        elif kind == "call_vote":
            if self.spoke_since_vote:
                self._call_vote()
            else:                      # no back-to-back ballots
                self._call_on(self._pick_speaker())
        elif (kind == "declare"
              and action.get("verdict") in ("guilty", "not_guilty", "hung")):
            counts = self.last_tally or {}
            if (action["verdict"] != "hung"
                    and counts.get(action["verdict"], 0) != 12):
                # premature declare without a unanimous ballot: rejected
                self._call_on(self._pick_speaker())
            else:
                self._declare(action["verdict"], action.get("reason", ""))
        else:                          # unknown/invalid action
            self._call_on(self._pick_speaker())

    def _pick_speaker(self, preferred=None):
        """Pick who speaks next. Honor `preferred` unless that juror spoke
        within SPEAKER_COOLDOWN turns; otherwise fall back to the juror who
        has been silent longest (never-spoken seats come first, by seat)."""
        on_cooldown = {s for s, idx in self._last_spoke.items()
                       if self._speak_idx - idx < SPEAKER_COOLDOWN}
        if preferred in self.cards and preferred not in on_cooldown:
            return preferred
        eligible = [s for s in sorted(self.cards) if s not in on_cooldown]
        if not eligible:               # everyone spoke recently: allow anyone
            eligible = sorted(self.cards)
        # least-recently-spoken wins; unseen seats default to 0 (earliest)
        return min(eligible, key=lambda s: self._last_spoke.get(s, 0))

    def _emit_trace(self, seat, reply):
        if "_prompt" in reply:
            self.emit({"type": "prompt", "seat": seat,
                       "system": reply["_prompt"]["system"],
                       "user": reply["_prompt"]["user"]})
        if "_raw_output" in reply:
            self.emit({"type": "reasoning", "seat": seat,
                       "raw": reply["_raw_output"],
                       "mode": "speak" if "speech" in reply else "vote"})

    # --- juror speaks ----------------------------------------------------
    def _call_on(self, seat):
        self._turns_since_vote += 1
        self._speak_idx += 1
        self._last_spoke[seat] = self._speak_idx
        card = self.cards[seat]
        self.emit({"type": "speaker", "seat": seat})
        try:
            reply = self.juror_fn(card, self.case_text, self.transcript,
                                  "speak", self.last_tally)
        except Exception:
            self._agent_failed()
            if self.verdict is None:
                self.emit({"type": "speech", "seat": seat,
                           "name": card["name"],
                           "speech": f"(Juror #{seat} passes.)"})
            return
        self._consec_failures = 0
        self._emit_trace(seat, reply)
        self.transcript.append({"seat": seat, "name": card["name"],
                                "speech": reply["speech"]})
        lean = reply.get("lean", "undecided")
        self.leans[seat] = {"lean": lean,
                            "confidence": reply.get("confidence", 0)}
        self.spoke_since_vote = True
        event = {"type": "speech", "seat": seat, "name": card["name"],
                 "speech": reply["speech"]}
        # Not the lean itself — just whether it now conflicts with the
        # last public ballot, so the room can hint a mind may be changing
        # without revealing where it's landed.
        last_vote = self.last_votes.get(seat)
        event["reconsidering"] = bool(
            last_vote and lean != "undecided" and lean != last_vote)
        if "audio" in reply:
            event["audio"] = reply["audio"]
        self.emit(event)

    # --- public ballot ---------------------------------------------------
    def _call_vote(self, binding=True):
        self._turns_since_vote = 0
        self.emit({"type": "vote_called", "binding": binding})
        seats = sorted(self.cards)

        def one_vote(seat):
            try:
                r = self.juror_fn(self.cards[seat], self.case_text,
                                  self.transcript, "vote", self.last_tally)
                v = r.get("vote")
                return (v if v in VOTE_VALUES else "undecided", r)
            except Exception:
                return ("undecided", {})

        results = {}
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = {pool.submit(one_vote, seat): seat for seat in seats}
            for future in as_completed(futures):
                seat = futures[future]
                results[seat] = future.result()
                # no vote value revealed here — just that the ballot is in,
                # so the room can show progress before the full tally
                self.emit({"type": "voter_done", "seat": seat})
        votes = {}
        reasons = {}
        for seat in seats:
            v, reply = results[seat]
            votes[seat] = v
            reasons[seat] = reply.get("reasoning", "") if reply else ""
            self._emit_trace(seat, reply)
        counts = tally(votes)
        self.last_tally = counts
        self.last_votes = votes
        self.last_vote_reasons = reasons
        self.spoke_since_vote = False
        self.emit({"type": "vote_result", "votes": votes, "tally": counts,
                   "reasons": reasons, "binding": binding})
        # a non-binding straw poll never ends the case, even if unanimous —
        # the room still has to deliberate
        if binding:
            result = unanimous(counts)
            if result:
                self._declare(result, "unanimous vote")

    # --- end of deliberation ----------------------------------------------
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
