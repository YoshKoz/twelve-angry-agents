import test from "node:test";
import assert from "node:assert";
import { initialState, applyEvent } from "../web/room_state.js";

const EMPTY_TALLY = { guilty: 0, not_guilty: 0, undecided: 0, abstain: 0 };

function replay(events, from = initialState()) {
  let s = from;
  for (const ev of events) s = applyEvent(s, ev);
  return s;
}

// One of every event the orchestrator can emit, in a plausible order.
const CANNED = [
  { type: "case", text: "the case" },
  { type: "roster", jurors: [
    { seat: 1, name: "Walt", occupation: "coach", emoji: "🏈" },
    { seat: 8, name: "Davis", occupation: "architect", emoji: "🤔" },
  ]},
  { type: "speaker", seat: 8 },
  { type: "speech", seat: 8, name: "Davis", speech: "Let's talk." },
  { type: "request", seat: 8, kind: "demand_vote",
    summary: "demands a vote by hands" },
  { type: "ruling", seat: 8, granted: true, reason: "fair",
    request: "demands a vote by hands" },
  { type: "vote_called", binding: true, method: "hands" },
  { type: "voter_done", seat: 1 },
  { type: "voter_done", seat: 8 },
  { type: "vote_result",
    votes: { 1: "guilty", 8: "not_guilty" },
    tally: { ...EMPTY_TALLY, guilty: 1, not_guilty: 1 },
    reasons: { 1: "sure", 8: "doubt" }, binding: true,
    method: "hands", secret: false },
  { type: "vote_change", seat: 1, vote: "not_guilty",
    tally: { ...EMPTY_TALLY, not_guilty: 2 } },
  { type: "record", kind: "evidence", seat: 8, available: true,
    text: "It is a switchblade." },
  { type: "prompt", seat: 8, system: "sys", user: "user txt" },
  { type: "reasoning", seat: 8, raw: '{"speech":"hi"}', mode: "speak" },
  { type: "verdict_announced", verdict: "not_guilty", reason: "we all agree" },
  { type: "judge_ruling", accepted: true, instruction: "So say you all." },
  { type: "verdict", verdict: "not_guilty", reason: "we all agree" },
];

test("replaying a canned transcript handles every event type", () => {
  const s = replay(CANNED);
  assert.equal(s.jurors.length, 2);
  assert.equal(s.activeSeat, 8);
  assert.equal(s.dialogue.speech, "Let's talk.");
  assert.equal(s.votes[1], "not_guilty");
  assert.equal(s.tally.not_guilty, 2);
  assert.equal(s.record.text, "It is a switchblade.");
  assert.equal(s.judge.accepted, true);
  assert.equal(s.verdict.verdict, "not_guilty");
});

test("unknown event type throws", () => {
  assert.throws(() => applyEvent(initialState(), { type: "nonsense" }));
});

test("applyEvent does not mutate the input state", () => {
  const s0 = initialState();
  applyEvent(s0, { type: "speaker", seat: 3 });
  assert.equal(s0.activeSeat, null);
  const s1 = replay([
    { type: "vote_result", votes: { 1: "guilty" }, tally: EMPTY_TALLY },
  ]);
  applyEvent(s1, { type: "vote_change", seat: 1, vote: "not_guilty",
                   tally: EMPTY_TALLY });
  assert.equal(s1.votes[1], "guilty");
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
    type: "roster",
    jurors: [{ seat: 1, name: "Walt", occupation: "c", emoji: "🏈" }],
  });
  assert.equal(s.jurors[0].emoji, "🏈");
});

test("prompt event stored", () => {
  const s = applyEvent(initialState(),
    { type: "prompt", seat: 8, system: "sys", user: "u" });
  assert.deepEqual(s.prompt, { seat: 8, system: "sys", user: "u" });
});

test("reasoning event stored", () => {
  const s = applyEvent(initialState(),
    { type: "reasoning", seat: 8, raw: '{"v":"g"}', mode: "vote" });
  assert.deepEqual(s.reasoning, { seat: 8, raw: '{"v":"g"}', mode: "vote" });
});

// --- requests from the floor and the foreman's rulings ---------------------

test("a request from the floor joins the open list", () => {
  const s = replay([
    { type: "request", seat: 8, kind: "request_evidence",
      summary: "sends out for: the knife" },
    { type: "request", seat: 5, kind: "demand_vote",
      summary: "demands a vote by secret" },
  ]);
  assert.equal(s.requests.length, 2);
  assert.deepEqual(s.requests[0], { seat: 8, kind: "request_evidence",
                                    summary: "sends out for: the knife" });
  assert.equal(s.requests[1].seat, 5);
});

test("a juror has only one open request: a new one replaces it", () => {
  const s = replay([
    { type: "request", seat: 8, kind: "demand_vote", summary: "first" },
    { type: "request", seat: 8, kind: "request_evidence", summary: "second" },
  ]);
  assert.equal(s.requests.length, 1);
  assert.equal(s.requests[0].kind, "request_evidence");
  assert.equal(s.requests[0].summary, "second");
});

test("a ruling clears that juror's request and records the outcome", () => {
  let s = replay([
    { type: "request", seat: 8, kind: "demand_vote", summary: "a vote" },
    { type: "request", seat: 5, kind: "request_evidence", summary: "the map" },
    { type: "ruling", seat: 8, granted: true, reason: "fair enough",
      request: "a vote" },
  ]);
  assert.deepEqual(s.requests.map((r) => r.seat), [5]);
  assert.deepEqual(s.ruling, { seat: 8, granted: true, reason: "fair enough",
                               request: "a vote" });
  s = applyEvent(s, { type: "ruling", seat: 5, granted: false,
                      reason: "not now", request: "the map" });
  assert.deepEqual(s.requests, []);
  assert.equal(s.ruling.granted, false);
});

test("a missing granted flag is read as refused, not as truthy", () => {
  const s = applyEvent(initialState(),
    { type: "ruling", seat: 8, reason: "hm", request: "a vote" });
  assert.equal(s.ruling.granted, false);
});

// --- the record ------------------------------------------------------------

test("record stores what the court read in", () => {
  let s = applyEvent(initialState(), {
    type: "record", kind: "evidence", seat: 8, available: true,
    text: "It is a switchblade.",
  });
  assert.deepEqual(s.record, { kind: "evidence", seat: 8, available: true,
                               text: "It is a switchblade." });
  s = applyEvent(s, { type: "record", kind: "experiment", seat: 5,
                      available: false, text: "The jury has no knife." });
  assert.equal(s.record.kind, "experiment");
  assert.equal(s.record.available, false);
});

// --- ballots ---------------------------------------------------------------

test("vote_called sets voting, vote_result clears it", () => {
  let s = applyEvent(initialState(), { type: "vote_called" });
  assert.equal(s.voting, true);
  s = applyEvent(s, { type: "vote_result", votes: {}, tally: EMPTY_TALLY });
  assert.equal(s.voting, false);
});

test("straw-poll ballot marks state non-binding, real ballot binding", () => {
  let s = applyEvent(initialState(), { type: "vote_called", binding: false });
  assert.equal(s.binding, false);
  s = applyEvent(s, { type: "vote_result", votes: {}, tally: EMPTY_TALLY,
                      binding: false });
  assert.equal(s.binding, false);
  s = applyEvent(s, { type: "vote_called", binding: true });
  assert.equal(s.binding, true);
});

test("the ballot method is carried on both vote events", () => {
  let s = applyEvent(initialState(),
    { type: "vote_called", binding: true, method: "secret" });
  assert.equal(s.method, "secret");
  s = applyEvent(s, { type: "vote_result", votes: {}, tally: EMPTY_TALLY,
                      method: "secret", secret: true });
  assert.equal(s.method, "secret");
  assert.equal(s.secret, true);
});

test("method defaults to a show of hands when unstated", () => {
  let s = applyEvent(initialState(), { type: "vote_called" });
  assert.equal(s.method, "hands");
  s = applyEvent(s, { type: "vote_result", votes: {}, tally: EMPTY_TALLY });
  assert.equal(s.method, "hands");
  assert.equal(s.secret, false);
});

test("a secret ballot clears the board: the room sees the count, not hands", () => {
  let s = replay([
    { type: "vote_result", votes: { 1: "guilty", 8: "not_guilty" },
      tally: { ...EMPTY_TALLY, guilty: 1, not_guilty: 1 },
      method: "hands", secret: false },
  ]);
  assert.equal(s.votes[1], "guilty");
  s = applyEvent(s, {
    type: "vote_result", votes: {},
    tally: { ...EMPTY_TALLY, not_guilty: 1, guilty: 1 },
    method: "secret", secret: true,
  });
  assert.deepEqual(s.votes, {});          // no stale hands left showing
  assert.equal(s.secret, true);
  assert.equal(s.tally.not_guilty, 1);    // the count is still public
});

test("a show of hands after a secret ballot puts the votes back on the board", () => {
  const s = replay([
    { type: "vote_result", votes: {}, tally: EMPTY_TALLY, method: "secret",
      secret: true },
    { type: "vote_result", votes: { 1: "guilty" },
      tally: { ...EMPTY_TALLY, guilty: 1 }, method: "hands", secret: false },
  ]);
  assert.equal(s.votes[1], "guilty");
  assert.equal(s.secret, false);
});

test("an abstention is a vote value the board can hold", () => {
  const s = applyEvent(initialState(), {
    type: "vote_result", votes: { 8: "abstain" },
    tally: { ...EMPTY_TALLY, abstain: 1 }, method: "secret", secret: false,
  });
  assert.equal(s.votes[8], "abstain");
  assert.equal(s.tally.abstain, 1);
});

test("voter_done tracks per-seat progress, clears on vote_result and next vote_called", () => {
  let s = applyEvent(initialState(), { type: "vote_called" });
  s = applyEvent(s, { type: "voter_done", seat: 3 });
  s = applyEvent(s, { type: "voter_done", seat: 8 });
  assert.equal(s.votedIn[3], true);
  assert.equal(s.votedIn[8], true);
  assert.equal(s.votedIn[1], undefined);
  s = applyEvent(s, { type: "vote_result", votes: {}, tally: EMPTY_TALLY });
  assert.deepEqual(s.votedIn, {});
  s = applyEvent(s, { type: "vote_called" });
  assert.deepEqual(s.votedIn, {});
});

// --- changing a vote on the floor -----------------------------------------

test("vote_change updates one seat and the tally, leaving the rest alone", () => {
  const s = replay([
    { type: "vote_result",
      votes: { 1: "guilty", 3: "guilty", 8: "not_guilty" },
      tally: { ...EMPTY_TALLY, guilty: 2, not_guilty: 1 },
      method: "hands", secret: false },
    { type: "vote_change", seat: 3, vote: "not_guilty",
      tally: { ...EMPTY_TALLY, guilty: 1, not_guilty: 2 } },
  ]);
  assert.equal(s.votes[3], "not_guilty");
  assert.equal(s.votes[1], "guilty");     // untouched
  assert.equal(s.votes[8], "not_guilty");
  assert.deepEqual(s.tally, { ...EMPTY_TALLY, guilty: 1, not_guilty: 2 });
});

test("a change of vote settles that juror's reconsidering hint", () => {
  const s = replay([
    { type: "speech", seat: 3, name: "Frank", speech: "hmm",
      reconsidering: true },
    { type: "speech", seat: 8, name: "Davis", speech: "go on",
      reconsidering: true },
    { type: "vote_change", seat: 3, vote: "not_guilty", tally: EMPTY_TALLY },
  ]);
  assert.equal(s.reconsidering[3], false);
  assert.equal(s.reconsidering[8], true);   // nobody else is settled
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
  s = applyEvent(s, { type: "vote_result", votes: {}, tally: EMPTY_TALLY });
  assert.deepEqual(s.reconsidering, {});
});

// --- the verdict and the bench ---------------------------------------------

test("verdict_announced holds the finding before the bench rules", () => {
  const s = applyEvent(initialState(), {
    type: "verdict_announced", verdict: "guilty", reason: "we all agree",
  });
  assert.deepEqual(s.announced, { verdict: "guilty", reason: "we all agree" });
  assert.equal(s.verdict, null);
  assert.equal(s.judge, null);
});

test("an accepted verdict keeps standing until the verdict lands", () => {
  const s = replay([
    { type: "verdict_announced", verdict: "guilty", reason: "unanimous" },
    { type: "judge_ruling", accepted: true, instruction: "So say you all." },
    { type: "verdict", verdict: "guilty", reason: "unanimous" },
  ]);
  assert.deepEqual(s.announced, { verdict: "guilty", reason: "unanimous" });
  assert.equal(s.judge.accepted, true);
  assert.equal(s.judge.instruction, "So say you all.");
  assert.deepEqual(s.verdict, { verdict: "guilty", reason: "unanimous" });
});

test("a refused verdict is taken back off the board", () => {
  const s = replay([
    { type: "verdict_announced", verdict: "guilty", reason: "im tired" },
    { type: "judge_ruling", accepted: false,
      instruction: "The count is not unanimous." },
  ]);
  assert.equal(s.announced, null);
  assert.deepEqual(s.judge, { accepted: false,
                              instruction: "The count is not unanimous." });
  assert.equal(s.verdict, null);
});

test("the room can be sent back and reach a verdict later", () => {
  const s = replay([
    { type: "verdict_announced", verdict: "guilty", reason: "im tired" },
    { type: "judge_ruling", accepted: false, instruction: "Go back." },
    { type: "speech", seat: 8, name: "Davis", speech: "Let's keep going." },
    { type: "verdict_announced", verdict: "not_guilty", reason: "12-0" },
    { type: "judge_ruling", accepted: true, instruction: "So say you all." },
    { type: "verdict", verdict: "not_guilty", reason: "12-0" },
  ]);
  assert.equal(s.announced.verdict, "not_guilty");
  assert.equal(s.judge.accepted, true);
  assert.equal(s.verdict.verdict, "not_guilty");
});

// --- the docket ------------------------------------------------------------
// The room works one exhibit at a time, and every juror reads each exhibit
// privately before anyone argues. That independent read is the whole point of
// the board, so it gets pinned here.

const DOCKET = [
  { id: "the_knife", name: "The switchblade", claim: "one of a kind" },
  { id: "the_old_man", name: "The old man", claim: "he heard the shout" },
];

test("the docket arrives whole, before any exhibit opens", () => {
  const s = replay([{ type: "docket", exhibits: DOCKET }]);
  assert.deepEqual(s.docket, DOCKET);
  assert.equal(s.docketClosed, false);
  assert.equal(s.exhibit, null);
});

test("the case event carries the title and the charge", () => {
  const s = replay([{ type: "case", text: "the record",
                      title: "The State v. the Defendant",
                      charge: "First-degree murder." }]);
  assert.equal(s.caseTitle, "The State v. the Defendant");
  assert.equal(s.caseCharge, "First-degree murder.");
  assert.equal(s.caseText, "the record");
});

test("an exhibit opening puts the room back into reading, from nothing", () => {
  const s = replay([
    { type: "docket", exhibits: DOCKET },
    { type: "exhibit", id: "the_knife", name: "The switchblade",
      claim: "one of a kind", record: "carved handle", index: 0, total: 2 },
    { type: "assessed", seat: 3, exhibit: "the_knife" },
    { type: "assessed", seat: 7, exhibit: "the_knife" },
  ]);
  assert.equal(s.exhibit.id, "the_knife");
  assert.equal(s.exhibit.index, 0);
  assert.equal(s.assessing, true);
  assert.deepEqual(s.assessedIn, { 3: true, 7: true });
});

test("positions land together and end the reading", () => {
  const s = replay([
    { type: "docket", exhibits: DOCKET },
    { type: "exhibit", id: "the_knife", name: "The switchblade",
      claim: "c", record: "r", index: 0, total: 2 },
    { type: "assessed", seat: 3, exhibit: "the_knife" },
    { type: "positions", exhibit: "the_knife",
      positions: { 3: "supports_guilt", 7: "raises_doubt" },
      reasons: { 3: "he bought it", 7: "anyone could buy one" },
      summary: "1 for the prosecution, 1 doubting it, 0 unmoved" },
  ]);
  assert.deepEqual(s.positions.the_knife,
                   { 3: "supports_guilt", 7: "raises_doubt" });
  assert.equal(s.reasons.the_knife[7], "anyone could buy one");
  assert.equal(s.summaries.the_knife,
               "1 for the prosecution, 1 doubting it, 0 unmoved");
  // the private read is over; the per-seat progress ticks are cleared
  assert.equal(s.assessing, false);
  assert.deepEqual(s.assessedIn, {});
});

test("each exhibit keeps its own positions as the docket moves on", () => {
  const s = replay([
    { type: "docket", exhibits: DOCKET },
    { type: "exhibit", id: "the_knife", name: "The switchblade",
      claim: "c", record: "r", index: 0, total: 2 },
    { type: "positions", exhibit: "the_knife",
      positions: { 1: "supports_guilt" }, reasons: {}, summary: "a" },
    { type: "exhibit_closed", id: "the_knife", name: "The switchblade",
      finding: "it is not one of a kind" },
    { type: "exhibit", id: "the_old_man", name: "The old man",
      claim: "c", record: "r", index: 1, total: 2 },
    { type: "positions", exhibit: "the_old_man",
      positions: { 1: "raises_doubt" }, reasons: {}, summary: "b" },
  ]);
  assert.equal(s.positions.the_knife[1], "supports_guilt");
  assert.equal(s.positions.the_old_man[1], "raises_doubt");
  assert.equal(s.findings.the_knife, "it is not one of a kind");
  assert.deepEqual(s.closed, ["the_knife"]);
  assert.equal(s.exhibit.id, "the_old_man");
});

test("closing the docket clears the bench and backfills every finding", () => {
  const s = replay([
    { type: "docket", exhibits: DOCKET },
    { type: "exhibit", id: "the_old_man", name: "The old man",
      claim: "c", record: "r", index: 1, total: 2 },
    { type: "docket_closed", findings: [
      { id: "the_knife", name: "The switchblade", summary: "not unique" },
      { id: "the_old_man", name: "The old man", summary: "he could not see" },
    ]},
  ]);
  assert.equal(s.docketClosed, true);
  assert.equal(s.exhibit, null);
  assert.equal(s.assessing, false);
  // the closing list is authoritative for anything the stream dropped
  assert.equal(s.findings.the_knife, "not unique");
  assert.equal(s.findings.the_old_man, "he could not see");
  assert.deepEqual(s.closed, ["the_knife", "the_old_man"]);
});

test("an exhibit the docket never announced still gets a row", () => {
  // replaying an old or partial transcript must not lose the exhibit
  const s = replay([
    { type: "exhibit", id: "the_alibi", name: "The alibi",
      claim: "he could not name the films", record: "r", index: 0, total: 1 },
  ]);
  assert.deepEqual(s.docket, [{ id: "the_alibi", name: "The alibi",
                                claim: "he could not name the films" }]);
});
