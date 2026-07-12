import test from "node:test";
import assert from "node:assert";
import { initialState, applyEvent } from "../web/room_state.js";

const CANNED = [
  { type: "case", text: "the case" },
  { type: "roster", jurors: [
    { seat: 1, name: "Walt", occupation: "coach" },
    { seat: 8, name: "Davis", occupation: "architect" },
  ]},
  { type: "vote_called" },
  { type: "vote_result",
    votes: { 1: "guilty", 8: "not_guilty" },
    tally: { guilty: 1, not_guilty: 1, undecided: 0 } },
  { type: "speaker", seat: 8 },
  { type: "speech", seat: 8, name: "Davis", speech: "Let's talk." },
  { type: "verdict", verdict: "hung", reason: "deadlock" },
];

test("replaying a canned transcript handles every event type", () => {
  let s = initialState();
  for (const ev of CANNED) s = applyEvent(s, ev);
  assert.equal(s.jurors.length, 2);
  assert.equal(s.activeSeat, 8);
  assert.equal(s.dialogue.speech, "Let's talk.");
  assert.equal(s.votes[1], "guilty");
  assert.equal(s.tally.guilty, 1);
  assert.equal(s.verdict.verdict, "hung");
});

test("vote_called sets voting, vote_result clears it", () => {
  let s = applyEvent(initialState(), { type: "vote_called" });
  assert.equal(s.voting, true);
  s = applyEvent(s, { type: "vote_result", votes: {},
                      tally: { guilty: 0, not_guilty: 0, undecided: 0 } });
  assert.equal(s.voting, false);
});

test("unknown event type throws", () => {
  assert.throws(() => applyEvent(initialState(), { type: "nonsense" }));
});

test("applyEvent does not mutate the input state", () => {
  const s0 = initialState();
  applyEvent(s0, { type: "speaker", seat: 3 });
  assert.equal(s0.activeSeat, null);
});

test("error event stored", () => {
  const s = applyEvent(initialState(), { type: "error", message: "boom" });
  assert.equal(s.error, "boom");
});
