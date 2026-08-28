// Pure event reducer for the visual novel. No DOM, no fetch. Imported by
// room.js (browser) and by the node smoke test.
//
// The room now works an EVIDENCE DOCKET: one exhibit at a time, every juror
// reading it privately first. That independent read — `positions` — is the
// centrepiece of the UI, so most of the new state here is per-exhibit and
// keyed by exhibit id.

export function initialState() {
  return {
    jurors: [],        // [{seat, name, occupation, emoji}]
    activeSeat: null,  // seat currently lit
    dialogue: null,    // {seat, name, speech} last spoken
    votes: {},         // seat -> "guilty"|"not_guilty"|"undecided"|"abstain"
    reconsidering: {}, // seat -> true if lean now conflicts with last public vote
    tally: null,       // {guilty, not_guilty, undecided, abstain}
    voting: false,     // between vote_called and vote_result
    binding: true,     // false while the current/last ballot is a straw poll
    method: "hands",   // how the current/last ballot is being taken
    secret: false,     // true when the last ballot hid who voted what
    votedIn: {},       // seat -> true once their ballot is in (value hidden)
    requests: [],      // open requests from the floor: {seat, kind, summary}
    ruling: null,      // {seat, granted, reason, request} foreman's last ruling
    record: null,      // {kind, seat, available, text} what the court read in
    announced: null,   // {verdict, reason} foreman's finding, not yet taken
    judge: null,       // {accepted, instruction} the bench's last word
    verdict: null,     // {verdict, reason}
    error: null,
    caseText: null,    // case file text
    caseTitle: "",     // "The State v. …"
    caseCharge: "",    // what the defendant is charged with
    prompt: null,      // {seat, system, user}
    reasoning: null,   // {seat, raw, mode}

    // --- the docket ------------------------------------------------------
    docket: [],        // [{id, name, claim}] every exhibit, in order
    exhibit: null,     // {id, name, claim, record, index, total} in front of the room
    assessing: false,  // between `exhibit` and `positions`: jurors reading privately
    assessedIn: {},    // seat -> true once their private read is in (value hidden)
    positions: {},     // exhibit id -> {seat: "supports_guilt"|"raises_doubt"|"inconclusive"}
    reasons: {},       // exhibit id -> {seat: one-line reason}
    summaries: {},     // exhibit id -> "6 for the prosecution, …"
    findings: {},      // exhibit id -> the foreman's closing finding
    closed: [],        // exhibit ids closed, in the order they were closed
    docketClosed: false,
  };
}

// An exhibit the docket did not announce (an old transcript, a partial
// replay) still deserves a row on the board.
function withExhibit(docket, ev) {
  if (docket.some((e) => e.id === ev.id)) return docket;
  return [...docket, { id: ev.id, name: ev.name, claim: ev.claim }];
}

export function applyEvent(state, ev) {
  const s = { ...state };
  switch (ev.type) {
    case "case":
      s.caseText = ev.text;
      s.caseTitle = ev.title || "";
      s.caseCharge = ev.charge || "";
      break;
    case "roster":
      s.jurors = ev.jurors;
      break;
    case "speaker":
      s.activeSeat = ev.seat;
      break;
    case "speech":
      s.dialogue = { seat: ev.seat, name: ev.name, speech: ev.speech };
      s.reconsidering = { ...state.reconsidering, [ev.seat]: !!ev.reconsidering };
      break;
    case "request":
      // one open request per juror: a new one replaces theirs
      s.requests = [
        ...state.requests.filter((r) => r.seat !== ev.seat),
        { seat: ev.seat, kind: ev.kind, summary: ev.summary },
      ];
      break;
    case "ruling":
      s.requests = state.requests.filter((r) => r.seat !== ev.seat);
      s.ruling = {
        seat: ev.seat, granted: !!ev.granted,
        reason: ev.reason, request: ev.request,
      };
      break;
    case "record":
      s.record = {
        kind: ev.kind, seat: ev.seat,
        available: !!ev.available, text: ev.text,
      };
      break;

    // --- the docket ------------------------------------------------------
    case "docket":
      s.docket = (ev.exhibits || []).map(
        (e) => ({ id: e.id, name: e.name, claim: e.claim }));
      s.docketClosed = false;
      break;
    case "exhibit":
      s.docket = withExhibit(state.docket, ev);
      s.exhibit = {
        id: ev.id, name: ev.name, claim: ev.claim, record: ev.record,
        index: ev.index, total: ev.total,
      };
      // a fresh exhibit: everyone reads it again, from nothing
      s.assessing = true;
      s.assessedIn = {};
      break;
    case "assessed":
      // like voter_done: progress only, the read itself stays private until
      // the whole room's positions land together
      s.assessedIn = { ...state.assessedIn, [ev.seat]: true };
      break;
    case "positions":
      s.positions = { ...state.positions, [ev.exhibit]: ev.positions || {} };
      s.reasons = { ...state.reasons, [ev.exhibit]: ev.reasons || {} };
      s.summaries = { ...state.summaries, [ev.exhibit]: ev.summary || "" };
      s.assessing = false;
      s.assessedIn = {};
      break;
    case "exhibit_closed":
      s.docket = withExhibit(state.docket, ev);
      s.findings = { ...state.findings, [ev.id]: ev.finding || "" };
      s.closed = state.closed.includes(ev.id)
        ? state.closed : [...state.closed, ev.id];
      break;
    case "docket_closed":
      s.docketClosed = true;
      s.exhibit = null;
      s.assessing = false;
      s.assessedIn = {};
      // the closing list is authoritative for anything the stream dropped
      s.findings = { ...state.findings };
      for (const f of ev.findings || []) {
        s.findings[f.id] = f.summary || s.findings[f.id] || "";
      }
      s.closed = [...state.closed];
      for (const f of ev.findings || []) {
        if (!s.closed.includes(f.id)) s.closed.push(f.id);
      }
      break;

    case "vote_called":
      s.voting = true;
      s.binding = ev.binding !== false;   // default binding unless told otherwise
      s.method = ev.method || "hands";
      s.votedIn = {};
      break;
    case "voter_done":
      s.votedIn = { ...state.votedIn, [ev.seat]: true };
      break;
    case "vote_result":
      // a secret ballot sends an empty votes map — the room sees the count
      // only, so the board must not keep showing the previous hands
      s.votes = ev.votes || {};
      s.tally = ev.tally;
      s.binding = ev.binding !== false;
      s.method = ev.method || "hands";
      s.secret = !!ev.secret;
      s.voting = false;
      s.votedIn = {};
      s.reconsidering = {};   // fresh ballot supersedes any pre-vote hints
      break;
    case "vote_change":
      // a juror switching sides on the floor, between ballots
      s.votes = { ...state.votes, [ev.seat]: ev.vote };
      s.tally = ev.tally;
      s.reconsidering = { ...state.reconsidering, [ev.seat]: false };
      break;
    case "verdict_announced":
      s.announced = { verdict: ev.verdict, reason: ev.reason };
      break;
    case "judge_ruling":
      s.judge = { accepted: !!ev.accepted, instruction: ev.instruction };
      if (!ev.accepted) s.announced = null;   // sent back, nothing stands
      break;
    case "verdict":
      s.verdict = { verdict: ev.verdict, reason: ev.reason };
      break;
    case "error":
      s.error = ev.message;
      break;
    case "prompt":
      s.prompt = { seat: ev.seat, system: ev.system, user: ev.user };
      break;
    case "reasoning":
      s.reasoning = { seat: ev.seat, raw: ev.raw, mode: ev.mode };
      break;
    default:
      throw new Error("unknown event type: " + ev.type);
  }
  return s;
}
