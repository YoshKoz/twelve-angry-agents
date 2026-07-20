import test from "node:test";
import assert from "node:assert";
import { initialState, applyEvent } from "../web/room_state.js";

const CANNED = [
  { type: "case", text: "the case" },
  { type: "roster", jurors: [
    { seat: 1, name: "Walt", occupation: "coach", emoji: "\uD83C\uDFC8" },
    { seat: 8, name: "Davis", occupation: "architect", emoji: "\uD83E\uDD14" },
  ]},
  { type: "vote_called" },
  { type: "vote_result",
    votes: { 1: "guilty", 8: "not_guilty" },
    tally: { guilty: 1, not_guilty: 1, undecided: 0 } },
  { type: "speaker", seat: 8 },
  { type: "speech", seat: 8, name: "Davis", speech: "Let's talk." },
  { type: "prompt", seat: 8, system: "sys", user: "user txt" },
  { type: "reasoning", seat: 8, raw: '{"speech":"hi"}', mode: "speak" },
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

test("straw-poll ballot marks state non-binding, real ballot binding", () => {
  let s = applyEvent(initialState(), { type: "vote_called", binding: false });
  assert.equal(s.binding, false);
  s = applyEvent(s, { type: "vote_result", votes: {},
                      tally: { guilty: 0, not_guilty: 0, undecided: 0 },
                      binding: false });
  assert.equal(s.binding, false);
  s = applyEvent(s, { type: "vote_called", binding: true });
  assert.equal(s.binding, true);
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

test("case event stores caseText", () => {
  const s = applyEvent(initialState(), { type: "case", text: "the case file" });
  assert.equal(s.caseText, "the case file");
});

test("roster includes emoji", () => {
  const s = applyEvent(initialState(), {
    type: "roster", jurors: [{ seat: 1, name: "Walt", occupation: "c", emoji: "\uD83C\uDFC8" }]
  });
  assert.equal(s.jurors[0].emoji, "\uD83C\uDFC8");
});

test("prompt event stored", () => {
  const s = applyEvent(initialState(), { type: "prompt", seat: 8, system: "sys", user: "u" });
  assert.equal(s.prompt.seat, 8);
  assert.equal(s.prompt.system, "sys");
  assert.equal(s.prompt.user, "u");
});

test("reasoning event stored", () => {
  const s = applyEvent(initialState(), { type: "reasoning", seat: 8, raw: '{"v":"g"}', mode: "vote" });
  assert.equal(s.reasoning.seat, 8);
  assert.equal(s.reasoning.raw, '{"v":"g"}');
  assert.equal(s.reasoning.mode, "vote");
});

test("speech sets reconsidering per seat, vote_result clears it", () => {
  let s = applyEvent(initialState(), {
    type: "speech", seat: 8, name: "Davis", speech: "hmm", reconsidering: true,
  });
  assert.equal(s.reconsidering[8], true);
  s = applyEvent(s, {
    type: "speech", seat: 3, name: "Cobb", speech: "no", reconsidering: false,
  });
  assert.equal(s.reconsidering[8], true);   // seat 8's flag persists
  assert.equal(s.reconsidering[3], false);
  s = applyEvent(s, { type: "vote_result", votes: {},
                      tally: { guilty: 0, not_guilty: 0, undecided: 0 } });
  assert.deepEqual(s.reconsidering, {});
});

test("voter_done tracks per-seat progress, clears on vote_result and next vote_called", () => {
  let s = applyEvent(initialState(), { type: "vote_called" });
  s = applyEvent(s, { type: "voter_done", seat: 3 });
  s = applyEvent(s, { type: "voter_done", seat: 8 });
  assert.equal(s.votedIn[3], true);
  assert.equal(s.votedIn[8], true);
  assert.equal(s.votedIn[1], undefined);
  s = applyEvent(s, { type: "vote_result", votes: {},
                      tally: { guilty: 0, not_guilty: 0, undecided: 0 } });
  assert.deepEqual(s.votedIn, {});
  s = applyEvent(s, { type: "vote_called" });
  assert.deepEqual(s.votedIn, {});
});
