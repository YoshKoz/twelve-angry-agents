// Pure event reducer for the visual novel. No DOM. Imported by room.js
// (browser) and by the node smoke test.

export function initialState() {
  return {
    jurors: [],        // [{seat, name, occupation}]
    activeSeat: null,  // seat currently lit
    dialogue: null,    // {seat, name, speech} last spoken
    votes: {},         // seat -> "guilty"|"not_guilty"|"undecided"
    tally: null,       // {guilty, not_guilty, undecided}
    voting: false,     // between vote_called and vote_result
    verdict: null,     // {verdict, reason}
    error: null,
  };
}

export function applyEvent(state, ev) {
  const s = { ...state };
  switch (ev.type) {
    case "case":
      break;                              // shown once at start; no state
    case "roster":
      s.jurors = ev.jurors;
      break;
    case "speaker":
      s.activeSeat = ev.seat;
      break;
    case "speech":
      s.dialogue = { seat: ev.seat, name: ev.name, speech: ev.speech };
      break;
    case "vote_called":
      s.voting = true;
      break;
    case "vote_result":
      s.votes = ev.votes;
      s.tally = ev.tally;
      s.voting = false;
      break;
    case "verdict":
      s.verdict = { verdict: ev.verdict, reason: ev.reason };
      break;
    case "error":
      s.error = ev.message;
      break;
    default:
      throw new Error("unknown event type: " + ev.type);
  }
  return s;
}
