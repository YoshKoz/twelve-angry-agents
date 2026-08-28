// Pure renderer of the event stream. All simulation state lives server-side;
// this file only queues events, reduces them via room_state.js, and paints.

import { initialState, applyEvent } from "./room_state.js";

let state = initialState();
const pending = [];       // events not yet shown
let busy = false;         // typewriter/pause in progress
let typeTimer = null;
let pauseTimer = null;
let pendingFullText = ""; // for click-to-skip
const AUTO_ADVANCE_MS = 1800;
const RULING_MS = 4500;   // how long the foreman's ruling stays on the board
const SWITCH_MS = 3200;   // how long a switched seat stays called out

// transient floor state: these are moments, not standing state, so they live
// here rather than in the reducer
let rulingVisible = false;
let rulingTimer = null;
let switchedSeat = null;
let switchTimer = null;

// what the film concluded about each exhibit, keyed by exhibit id. Fetched
// from /film/<case_id> and used for display ONLY — this text must never be
// sent anywhere near a prompt, which is why it is served separately from the
// case and never travels back to the server.
let filmFindings = {};
let filmCaseId = null;

// which board row is expanded
let openExhibit = null;

const el = (id) => document.getElementById(id);

const VERDICT_LABELS = {
  guilty: "CONVICTED",
  not_guilty: "ACQUITTED",
  hung: "HUNG JURY",
  stopped: "DELIBERATION STOPPED",
  aborted: "DELIBERATION ABORTED",
};

const REQUEST_LABELS = {
  demand_vote: "demands a vote",
  request_evidence: "asks the court for evidence",
  propose_experiment: "proposes an experiment",
  challenge: "challenges a juror",
};

const POSITION_LABELS = {
  supports_guilt: "supports guilt",
  raises_doubt: "raises doubt",
  inconclusive: "inconclusive",
};

const TRACE_MODES = {
  speak: "a speaking turn",
  vote: "a ballot",
  assess: "a private read of an exhibit",
  decide: "a decision",
};

const nameOf = (seat) => {
  const j = state.jurors.find((x) => x.seat === seat);
  return j ? j.name : `Juror #${seat}`;
};

// ---- playback pacing -------------------------------------------------------
// The slider governs PLAYBACK only: how fast text types and how long the room
// holds on a beat. It cannot make the agents think faster — nearly all the
// wall-clock in a live run is an LLM call — so it is labelled and stepped as
// what it is, with an explicit instant setting at the end of the range.
const SPEEDS = [
  { label: "Very slow", ms: 55, scale: 2.0 },
  { label: "Slow",      ms: 38, scale: 1.4 },
  { label: "Normal",    ms: 25, scale: 1.0 },
  { label: "Brisk",     ms: 14, scale: 0.6 },
  { label: "Fast",      ms: 7,  scale: 0.35 },
  { label: "Very fast", ms: 3,  scale: 0.15 },
  { label: "Instant — skip animation", ms: 0, scale: 0 },
];

function speedSetting() {
  const i = Number(el("speed").value);
  return SPEEDS[Math.min(Math.max(i, 0), SPEEDS.length - 1)] || SPEEDS[2];
}
const charMs = () => speedSetting().ms;
const instant = () => speedSetting().ms === 0;
// every fixed beat in this file goes through here, so the slider moves all of
// the pacing and not just the typewriter
const beatMs = (base) => Math.round(base * speedSetting().scale);

function renderSpeedLabel() {
  el("speed-value").textContent = speedSetting().label;
}

function endRun() {
  if (eventSource) { eventSource.close(); eventSource = null; }
  el("start").disabled = false;
  el("stop").disabled = true;
  el("dialogue").classList.remove("waiting");
  populateTranscriptSelect();   // the run that just ended is now on disk
}

function buildSeats() {
  const box = el("seats");
  box.innerHTML = "";
  for (const j of state.jurors) {
    const d = document.createElement("div");
    d.className = "seat";
    d.id = "seat-" + j.seat;
    d.dataset.vote = "unknown";
    d.innerHTML =
      `<div class="avatar">${j.emoji || j.seat}</div>` +
      `<div class="plate">${j.name}<br>${j.occupation}</div>`;
    box.appendChild(d);
  }
}

function render() {
  if (state.jurors.length && !el("seat-" + state.jurors[0].seat)) buildSeats();
  for (const j of state.jurors) {
    const d = el("seat-" + j.seat);
    d.classList.toggle("active", state.activeSeat === j.seat);
    d.dataset.vote = state.votes[j.seat] || "unknown";
    d.classList.toggle("reconsidering", !!state.reconsidering[j.seat]);
    d.classList.toggle("voted-in", state.voting && !!state.votedIn[j.seat]);
    d.classList.toggle("read-in", state.assessing && !!state.assessedIn[j.seat]);
    d.classList.toggle("switched", switchedSeat === j.seat);
  }
  if (state.caseTitle) el("docket-case").textContent = state.caseTitle;
  el("docket-charge").textContent = state.caseCharge || "";
  if (state.tally) {
    const abstain = state.tally.abstain || 0;
    const t = el("tally");
    t.textContent =
      `GUILTY ${state.tally.guilty} — NOT GUILTY ${state.tally.not_guilty}` +
      (abstain ? ` — ABSTAIN ${abstain}` : "");
    t.classList.remove("bump");
    void t.offsetWidth;               // restart animation
    t.classList.add("bump");
    // ballot slips: guilty fills from the left, acquittal from the right,
    // blank slips (secret ballot only) sit next to the guilty block
    const segs = el("tally-bar").children;
    for (let i = 0; i < segs.length; i++) {
      segs[i].className = "seg " +
        (i < state.tally.guilty ? "seg-guilty"
         : i < state.tally.guilty + abstain ? "seg-abstain"
         : i >= segs.length - state.tally.not_guilty ? "seg-acquit"
         : "seg-open");
    }
    // how it was taken. On a secret ballot the seats deliberately show
    // nothing, so the board has to say why it went blank.
    const note = el("ballot-note");
    note.dataset.secret = state.secret ? "yes" : "no";
    note.textContent =
      (state.secret ? "Secret written ballot — the count only"
                    : "Show of hands") +
      (state.binding ? "" : " · straw poll, not binding");
  }
  if (state.verdict) {
    el("verdict-text").textContent =
      VERDICT_LABELS[state.verdict.verdict] ||
      state.verdict.verdict.toUpperCase();
    el("verdict-reason").textContent = state.verdict.reason || "";
    const v = el("verdict");
    v.dataset.verdict = state.verdict.verdict;
    v.classList.add("show");
    el("verdict-card").focus();
    endRun();
  }
  if (state.error) {
    el("dialogue-name").textContent = "ERROR";
    el("dialogue-text").textContent = state.error;
    endRun();
  }
  renderFloor();
  renderBoard();
  renderSidePanel();
}

function renderFloor() {
  // a juror switching sides out loud — flagged on its own line and on the
  // seat, so it never reads as a routine ballot repaint
  const sw = el("switch-note");
  sw.classList.toggle("show", switchedSeat !== null);
  if (switchedSeat !== null) {
    const vote = state.votes[switchedSeat];
    sw.dataset.vote = vote || "unknown";
    sw.textContent =
      `Juror #${switchedSeat} — ${nameOf(switchedSeat)} changes his vote` +
      (vote ? ` to ${vote === "guilty" ? "GUILTY" : "NOT GUILTY"}` : "");
  }

  // what the floor has put to the foreman and he has not ruled on yet
  const requests = el("requests");
  requests.classList.toggle("show", state.requests.length > 0);
  const list = el("request-list");
  list.innerHTML = "";
  for (const r of state.requests) {
    const li = document.createElement("li");
    li.className = "request";
    li.dataset.kind = r.kind;
    const seat = document.createElement("span");
    seat.className = "request-seat";
    seat.textContent = "#" + r.seat;
    const kind = document.createElement("span");
    kind.className = "request-kind";
    kind.textContent = REQUEST_LABELS[r.kind] || r.kind;
    const summary = document.createElement("span");
    summary.className = "request-summary";
    summary.textContent = r.summary || "";
    li.append(seat, kind, summary);
    list.appendChild(li);
  }

  // the foreman's ruling on one of them — transient, see flashRuling()
  const ruling = el("ruling");
  ruling.classList.toggle("show", rulingVisible && !!state.ruling);
  if (state.ruling) {
    ruling.dataset.granted = state.ruling.granted ? "yes" : "no";
    el("ruling-verb").textContent =
      state.ruling.granted ? "GRANTED" : "REFUSED";
    el("ruling-request").textContent =
      `Juror #${state.ruling.seat} — ${state.ruling.request || ""}`;
    el("ruling-reason").textContent = state.ruling.reason || "";
  }

  // the court answering: read back from the case file, or the room trying
  // it out. Nobody claimed this — it is on the paper, so it looks like paper.
  const record = el("record");
  record.classList.toggle("show", !!state.record);
  if (state.record) {
    const experiment = state.record.kind === "experiment";
    record.dataset.kind = state.record.kind;
    record.dataset.available = state.record.available ? "yes" : "no";
    el("record-head").textContent =
      (experiment ? "The room tries it" : "Read back from the case file") +
      (state.record.seat ? ` — at the request of Juror #${state.record.seat}` : "") +
      (state.record.available ? ""
       : experiment ? " — cannot be done" : " — not in the record");
    el("record-text").textContent = state.record.text || "";
  }

  // a finding the foreman has announced but the bench has not taken yet
  const announced = el("announced");
  announced.classList.toggle("show", !!state.announced && !state.verdict);
  if (state.announced) {
    announced.textContent =
      `The foreman announces ${VERDICT_LABELS[state.announced.verdict] ||
        state.announced.verdict.toUpperCase()} — awaiting the bench`;
  }

  // the bench. A rejection has to be loud: deliberation carries on.
  const judge = el("judge");
  judge.classList.toggle("show", !!state.judge && !state.verdict);
  if (state.judge) {
    judge.dataset.accepted = state.judge.accepted ? "yes" : "no";
    judge.textContent = state.judge.accepted
      ? "The bench takes the verdict."
      : "SENT BACK BY THE BENCH — " +
        (state.judge.instruction || "continue deliberating.");
  }
}

// ---- the position board ----------------------------------------------------

// Which seats broke from the rest of the room on this exhibit. This is the
// whole point of the board: one agent reading the same paper differently is
// the interesting event, and it should not need counting by eye.
function outliers(positions) {
  const counts = {};
  for (const p of Object.values(positions)) counts[p] = (counts[p] || 0) + 1;
  let best = null, bestN = 0;
  for (const [p, n] of Object.entries(counts)) {
    if (n > bestN) { best = p; bestN = n; }
  }
  const total = Object.values(positions).length;
  // a genuinely split room has no outliers — only a clear majority does
  if (!best || bestN <= total / 2) return new Set();
  const out = new Set();
  for (const [seat, p] of Object.entries(positions)) {
    if (p !== best) out.add(Number(seat));
  }
  return out;
}

function seatOrder() {
  if (state.jurors.length) return state.jurors.map((j) => j.seat);
  return Array.from({ length: 12 }, (_, i) => i + 1);
}

function renderBoard() {
  const box = el("board");
  const seats = seatOrder();
  const current = state.exhibit ? state.exhibit.id : null;

  // the exhibit currently in front of the room
  const now = el("exhibit-now");
  now.classList.toggle("show", !!state.exhibit || state.docketClosed);
  if (state.docketClosed && !state.exhibit) {
    el("exhibit-now-head").textContent = "The docket is closed";
    el("exhibit-now-name").textContent = "What remains is the verdict.";
    el("exhibit-now-claim").textContent = "";
    el("exhibit-now-progress").textContent = "";
  } else if (state.exhibit) {
    const e = state.exhibit;
    el("exhibit-now-head").textContent =
      `Exhibit ${(e.index ?? 0) + 1} of ${e.total ?? state.docket.length}` +
      " — now before the room";
    el("exhibit-now-name").textContent = e.name || e.id;
    el("exhibit-now-claim").textContent = e.claim || "";
    const done = Object.keys(state.assessedIn).length;
    el("exhibit-now-progress").textContent = state.assessing
      ? `${done} of ${seats.length} jurors have read it privately…`
      : "";
  }

  box.innerHTML = "";
  if (!state.docket.length) {
    const empty = document.createElement("div");
    empty.className = "board-empty";
    empty.textContent =
      "The docket appears here once a deliberation starts.";
    box.appendChild(empty);
    return;
  }

  state.docket.forEach((ex, i) => {
    const positions = state.positions[ex.id] || {};
    const reasons = state.reasons[ex.id] || {};
    const out = outliers(positions);

    const row = document.createElement("div");
    row.className = "board-row";
    row.dataset.exhibit = ex.id;
    if (ex.id === current) row.classList.add("current");
    if (state.findings[ex.id] !== undefined) row.classList.add("closed");
    if (openExhibit === ex.id) row.classList.add("open");

    const head = document.createElement("button");
    head.className = "board-row-head";
    head.type = "button";
    head.setAttribute("aria-expanded", openExhibit === ex.id ? "true" : "false");
    const num = document.createElement("span");
    num.className = "board-ex-num";
    num.textContent = i + 1;
    const name = document.createElement("span");
    name.className = "board-ex-name";
    name.textContent = ex.name || ex.id;
    const sum = document.createElement("span");
    sum.className = "board-ex-sum";
    sum.textContent = state.summaries[ex.id] || "";
    head.append(num, name, sum);
    head.addEventListener("click", () => {
      openExhibit = openExhibit === ex.id ? null : ex.id;
      renderBoard();
    });

    const cells = document.createElement("div");
    cells.className = "board-cells";
    for (const seat of seats) {
      const c = document.createElement("span");
      c.className = "board-cell";
      const p = positions[seat];
      c.dataset.pos = p || (ex.id === current && state.assessedIn[seat]
                            ? "read" : "pending");
      if (out.has(seat)) c.classList.add("outlier");
      c.textContent = seat;
      c.title = p
        ? `Juror #${seat} — ${nameOf(seat)}: ${POSITION_LABELS[p] || p}` +
          (reasons[seat] ? `\n${reasons[seat]}` : "")
        : `Juror #${seat} — ${nameOf(seat)}: not read yet`;
      cells.appendChild(c);
    }

    row.append(head, cells);

    if (openExhibit === ex.id) {
      row.appendChild(exhibitDetail(ex, positions, reasons, out, seats));
    }
    box.appendChild(row);
  });
}

function exhibitDetail(ex, positions, reasons, out, seats) {
  const d = document.createElement("div");
  d.className = "board-detail";

  const add = (cls, label, text) => {
    if (!text) return;
    const wrap = document.createElement("div");
    wrap.className = "detail-block " + cls;
    const l = document.createElement("div");
    l.className = "detail-label";
    l.textContent = label;
    const t = document.createElement("div");
    t.className = "detail-text";
    t.textContent = text;
    wrap.append(l, t);
    d.appendChild(wrap);
  };

  add("claim", "The prosecution says", ex.claim || "");
  add("ours", "This jury of agents concluded",
      [state.summaries[ex.id], state.findings[ex.id]]
        .filter(Boolean).join(" — "));
  // display only, never sent anywhere: see filmFindings above
  add("film", "In the film", filmFindings[ex.id] || "");
  if (!filmFindings[ex.id]) {
    add("film", "In the film", "— no comparison on file for this exhibit —");
  }

  if (Object.keys(positions).length) {
    const l = document.createElement("div");
    l.className = "detail-label";
    l.textContent = "Each juror's own read, before anyone spoke";
    d.appendChild(l);
    const ul = document.createElement("ul");
    ul.className = "detail-reasons";
    for (const seat of seats) {
      const p = positions[seat];
      if (!p) continue;
      const li = document.createElement("li");
      li.dataset.pos = p;
      if (out.has(seat)) li.classList.add("outlier");
      const who = document.createElement("span");
      who.className = "detail-seat";
      who.textContent = `#${seat} ${nameOf(seat)}`;
      const pos = document.createElement("span");
      pos.className = "detail-pos";
      pos.textContent = POSITION_LABELS[p] || p;
      const why = document.createElement("span");
      why.className = "detail-why";
      why.textContent = reasons[seat] || "";
      li.append(who, pos, why);
      ul.appendChild(li);
    }
    d.appendChild(ul);
  }
  return d;
}

// hold the room on a beat that has no typewriter of its own
function beat(ms) {
  busy = true;
  pauseTimer = setTimeout(() => {
    pauseTimer = null;
    busy = false;
    showNext();
  }, beatMs(ms));
}

function flashRuling() {
  rulingVisible = true;
  if (rulingTimer) clearTimeout(rulingTimer);
  rulingTimer = setTimeout(() => {
    rulingTimer = null;
    rulingVisible = false;
    renderFloor();
  }, Math.max(400, beatMs(RULING_MS)));
  renderFloor();
}

function flashSwitch(seat) {
  switchedSeat = seat;
  if (switchTimer) clearTimeout(switchTimer);
  switchTimer = setTimeout(() => {
    switchTimer = null;
    switchedSeat = null;
    render();
  }, Math.max(400, beatMs(SWITCH_MS)));
  render();
}

function renderSidePanel() {
  // case file (strip markdown heading markers for display)
  if (state.caseText) {
    el("case-text").textContent =
      state.caseText.replace(/^#+\s*/gm, "").replace(/\*\*/g, "");
  }
  // prompt
  if (state.prompt) {
    el("prompt-system").textContent = state.prompt.system || "";
    el("prompt-user").textContent = state.prompt.user || "";
  }
  // reasoning
  if (state.reasoning) {
    el("reasoning-text").textContent = state.reasoning.raw || "";
  }
  const who = state.reasoning || state.prompt;
  if (who) {
    const seat = who.seat;
    const label = typeof seat === "number"
      ? `Juror #${seat} — ${nameOf(seat)}` : String(seat || "");
    const mode = state.reasoning && TRACE_MODES[state.reasoning.mode];
    el("trace-who").textContent =
      ` (${label}${mode ? ", " + mode : ""})`;
  }
}

function typewriter(text, done) {
  const box = el("dialogue-text");
  pendingFullText = text;
  if (instant()) {           // the explicit "skip animation" end of the slider
    box.textContent = text;
    done();
    return;
  }
  box.textContent = "";
  let i = 0;
  // setTimeout (not setInterval) so the speed slider is re-read every
  // character — dragging it mid-message takes effect immediately, not
  // just on the next speech.
  function tick() {
    if (instant()) {         // dragged to instant mid-line
      box.textContent = text;
      typeTimer = null;
      done();
      return;
    }
    box.textContent = text.slice(0, ++i);
    if (i >= text.length) {
      typeTimer = null;
      done();
      return;
    }
    typeTimer = setTimeout(tick, charMs());
  }
  typeTimer = setTimeout(tick, charMs());
}

let announcerAudio = null;   // clerk/foreman reading; click dialogue to skip
let speechAudio = null;      // whichever juror is currently speaking
let currentAudio = null;     // whatever is actually audible right now
let eventSource = null;

function playAudio(dataUrl) {
  // only one voice plays at a time — stop whatever's still going first
  if (currentAudio) currentAudio.pause();
  const a = new Audio(dataUrl);
  a.volume = 0.7;
  a.play().catch(() => {});
  currentAudio = a;
  return a;
}

function stopAllAudio() {
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  announcerAudio = null;
  speechAudio = null;
}

// The room runs no faster than the agents think, and a minute of silence
// between events is normal. Left alone the dialogue box just holds the last
// line, which reads as a dead room — most sharply right after skipping the
// clerk's reading, when the first juror has not answered yet.
function showWaiting() {
  if (!eventSource || state.verdict) return;
  if (state.assessing) {
    const done = Object.keys(state.assessedIn).length;
    el("dialogue-name").textContent = "THE JURY ROOM";
    el("dialogue-text").textContent =
      `reading the exhibit — ${done} of ${state.jurors.length || 12} done…`;
    el("dialogue").classList.add("waiting");
    return;
  }
  const juror = state.jurors.find((j) => j.seat === state.activeSeat);
  el("dialogue-name").textContent =
    juror ? `Juror #${juror.seat} — ${juror.name}` : "THE JURY ROOM";
  el("dialogue-text").textContent =
    juror ? "is thinking it over…" : "the room waits…";
  el("dialogue").classList.add("waiting");
}

function showNext() {
  if (busy) return;
  if (pending.length === 0) {
    showWaiting();
    return;
  }
  el("dialogue").classList.remove("waiting");
  const ev = pending.shift();
  state = applyEvent(state, ev);
  render();
  if (ev.type === "speech") {
    busy = true;
    el("dialogue-name").textContent = `Juror #${ev.seat} — ${ev.name}`;
    if (ev.audio) speechAudio = playAudio(ev.audio);
    typewriter(ev.speech, () => {
      pauseTimer = setTimeout(() => {
        pauseTimer = null;
        busy = false;
        showNext();
      }, beatMs(AUTO_ADVANCE_MS));
    });
  } else if (ev.type === "case") {
    // clerk reads the case aloud; queue waits, click dialogue to skip
    el("dialogue-name").textContent = "CLERK OF THE COURT";
    el("dialogue-text").textContent =
      "The clerk reads the case into the record… (click to skip)";
    if (ev.audio && !instant()) {
      busy = true;
      announcerAudio = playAudio(ev.audio);
      announcerAudio.addEventListener("ended", () => {
        announcerAudio = null;
        busy = false;
        showNext();
      });
    } else {
      showNext();
    }
  } else if (ev.type === "docket") {
    const n = (ev.exhibits || []).length;
    el("dialogue-name").textContent = "COURT OFFICER";
    el("dialogue-text").textContent =
      `${n} exhibit${n === 1 ? "" : "s"} on the docket. The room takes them one at a time.`;
    beat(AUTO_ADVANCE_MS);
  } else if (ev.type === "exhibit") {
    el("dialogue-name").textContent =
      `EXHIBIT ${(ev.index ?? 0) + 1} OF ${ev.total ?? "?"} — ${ev.name || ev.id}`;
    el("dialogue-text").textContent = ev.claim || "";
    beat(AUTO_ADVANCE_MS);
  } else if (ev.type === "assessed") {
    // twelve of these arrive in a burst: they are a progress tick on the
    // board and the seats, never a beat of their own
    showNext();
  } else if (ev.type === "positions") {
    el("dialogue-name").textContent = "THE ROOM READS IT — INDEPENDENTLY";
    el("dialogue-text").textContent =
      (ev.summary || "") + " — before anyone has spoken.";
    beat(AUTO_ADVANCE_MS);
  } else if (ev.type === "exhibit_closed") {
    el("dialogue-name").textContent = "FOREMAN";
    el("dialogue-text").textContent =
      `That one's settled. ${ev.finding || ""}`;
    beat(AUTO_ADVANCE_MS);
  } else if (ev.type === "docket_closed") {
    el("dialogue-name").textContent = "FOREMAN";
    el("dialogue-text").textContent =
      "That's the whole docket. All that's left is the verdict.";
    beat(AUTO_ADVANCE_MS);
  } else if (ev.type === "vote_called") {
    el("dialogue-name").textContent = "FOREMAN";
    el("dialogue-text").textContent = "Alright — let's take a vote.";
    if (ev.audio && !instant()) playAudio(ev.audio);
    beat(AUTO_ADVANCE_MS / 2);
  } else if (ev.type === "request") {
    el("dialogue-name").textContent = `Juror #${ev.seat} — ${nameOf(ev.seat)}`;
    el("dialogue-text").textContent =
      "(puts it to the foreman) " + (ev.summary || "");
    beat(AUTO_ADVANCE_MS / 2);
  } else if (ev.type === "ruling") {
    el("dialogue-name").textContent = "FOREMAN";
    el("dialogue-text").textContent =
      (ev.granted ? "Granted. " : "Overruled. ") + (ev.reason || "");
    flashRuling();
    beat(AUTO_ADVANCE_MS / 2);
  } else if (ev.type === "record") {
    // the record card carries the text; the dialogue only points at it
    el("dialogue-name").textContent =
      ev.kind === "experiment" ? "THE JURY ROOM" : "COURT OFFICER";
    el("dialogue-text").textContent = ev.kind === "experiment"
      ? "The room tries it and enters the result in the record."
      : "The officer reads back from the case file.";
    beat(AUTO_ADVANCE_MS);
  } else if (ev.type === "vote_change") {
    flashSwitch(ev.seat);
    beat(AUTO_ADVANCE_MS);
  } else if (ev.type === "verdict_announced") {
    el("dialogue-name").textContent = "FOREMAN";
    el("dialogue-text").textContent = "Your Honour, we have a verdict.";
    beat(AUTO_ADVANCE_MS / 2);
  } else if (ev.type === "judge_ruling") {
    el("dialogue-name").textContent = "THE BENCH";
    el("dialogue-text").textContent = ev.accepted
      ? "The court accepts the verdict."
      : (ev.instruction || "Go back and keep deliberating.");
    beat(ev.accepted ? AUTO_ADVANCE_MS / 2 : AUTO_ADVANCE_MS);
  } else {
    showNext();
  }
}

// Skip: cut the clerk's reading short, snap the typewriter to full text,
// or drop the auto-advance pause.
function skipAhead() {
  if (announcerAudio) {
    announcerAudio.pause();
    if (currentAudio === announcerAudio) currentAudio = null;
    announcerAudio = null;
    busy = false;
    showNext();
  } else if (typeTimer) {
    clearTimeout(typeTimer);
    typeTimer = null;
    el("dialogue-text").textContent = pendingFullText;
    pauseTimer = setTimeout(() => {
      pauseTimer = null;
      busy = false;
      showNext();
    }, beatMs(AUTO_ADVANCE_MS));
  } else if (pauseTimer) {
    clearTimeout(pauseTimer);
    pauseTimer = null;
    busy = false;
    showNext();
  }
}

// The whole room skips, not just the dialogue box. The floor strip sits
// between the seats and the dialogue, so binding this to #dialogue alone
// meant clicks aimed at the middle of the room landed on a request or a
// record card and did nothing.
// the controls, and the side panel you read the case file, the board and
// the traces in
const NO_SKIP = "button, input, select, textarea, a, label, #verdict, #sidepanel";
el("stage").addEventListener("click", (e) => {
  if (e.target.closest(NO_SKIP)) return;
  skipAhead();
});
document.addEventListener("keydown", (e) => {
  if (e.target.closest(NO_SKIP)) return;
  if (e.key === " " || e.key === "Enter" || e.key === "ArrowRight") {
    e.preventDefault();
    skipAhead();
  }
});

// Test hook. The room advances on its own timers, so "the text changed" does
// not prove a click did it — a browser test needs to watch the queue drain.
window.__room = {
  queued: () => pending.length,
  busy: () => busy,
  // a skip mid-typewriter snaps the line to full text instead of advancing
  // the queue, so a test has to know which of the two it should expect
  typing: () => typeTimer !== null,
  // playback pacing, so a test can assert the slider actually governs it
  speed: () => ({ ...speedSetting(), instant: instant() }),
  // the docket surface
  exhibit: () => state.exhibit,
  positions: () => state.positions,
  film: () => ({ ...filmFindings }),
  running: () => eventSource !== null,
};

// Case file collapse toggle.
el("case-toggle").addEventListener("click", () => {
  el("case-panel").classList.toggle("collapsed");
});

// The raw trace is the machinery, not the story: collapsed until asked for.
function toggleTrace() {
  const panel = el("trace-panel");
  const open = panel.classList.toggle("collapsed") === false;
  el("trace-toggle-text").textContent =
    open ? "Hide the raw model prompts" : "Show the raw model prompts";
  el("trace-toggle").setAttribute("aria-expanded", open ? "true" : "false");
}
el("trace-toggle").addEventListener("click", toggleTrace);
el("trace-toggle").addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleTrace(); }
});

el("speed").addEventListener("input", renderSpeedLabel);

// Dismiss the verdict card by clicking the dimmed backdrop around it
// (not the card itself) or pressing Escape.
el("verdict").addEventListener("click", (e) => {
  if (e.target === el("verdict")) el("verdict").classList.remove("show");
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") el("verdict").classList.remove("show");
});

function enqueue(ev) {
  pending.push(ev);
  showNext();
}

function clearTimers() {
  if (typeTimer) { clearTimeout(typeTimer); typeTimer = null; }
  if (pauseTimer) { clearTimeout(pauseTimer); pauseTimer = null; }
  if (rulingTimer) { clearTimeout(rulingTimer); rulingTimer = null; }
  if (switchTimer) { clearTimeout(switchTimer); switchTimer = null; }
}

function resetRoom() {
  if (eventSource) { eventSource.close(); eventSource = null; }
  stopAllAudio();
  clearTimers();
  rulingVisible = false;
  switchedSeat = null;
  openExhibit = null;
  pending.length = 0;
  busy = false;
  state = initialState();
  el("verdict").classList.remove("show");
  el("ballot-note").textContent = "";
  el("dialogue").classList.remove("waiting");
  renderFloor();       // clear last run's requests/ruling/record off the floor
  renderBoard();       // and last run's docket off the board
}

// What the film concluded, for the comparison column. Display only — it is
// never echoed back to the server and never reaches a prompt.
async function loadFilm(caseId) {
  if (!caseId || caseId === filmCaseId) return;
  try {
    const resp = await fetch(`/film/${encodeURIComponent(caseId)}`);
    if (!resp.ok) return;
    const body = await resp.json();
    filmFindings = body.findings || {};
    filmCaseId = caseId;
    renderBoard();
  } catch {
    filmFindings = {};
  }
}

el("case-select").addEventListener("change", (e) => loadFilm(e.target.value));

el("start").addEventListener("click", async () => {
  resetRoom();
  el("start").disabled = true;
  el("stop").disabled = false;
  el("dialogue-name").textContent = "CLERK OF THE COURT";
  el("dialogue-text").textContent = "Court is convening…";
  const caseId = el("case-select").value;
  loadFilm(caseId);
  const url = "/start" + (caseId ? `?case_id=${encodeURIComponent(caseId)}` : "");
  // /start must resolve (clearing the server's event history) before we
  // open the SSE connection — otherwise a fresh EventSource replays the
  // PREVIOUS run's leftover history, including its final verdict, right
  // back into the just-reset room.
  const resp = await fetch(url, { method: "POST" });
  const body = await resp.json();
  if (body.status === "error" || body.status === "already-running") {
    el("dialogue-name").textContent = "ERROR";
    el("dialogue-text").textContent = body.message || "A deliberation is already running.";
    el("start").disabled = false;
    el("stop").disabled = true;
    return;
  }
  eventSource = new EventSource("/events");
  eventSource.onmessage = (msg) => enqueue(JSON.parse(msg.data));
});

// Stop is immediate on this side: the server aborts within one agent call,
// and the UI must not sit there looking alive (or, worse, dead with Start
// still greyed out) while that lands.
el("stop").addEventListener("click", async () => {
  stopAllAudio();
  clearTimers();
  if (eventSource) { eventSource.close(); eventSource = null; }
  pending.length = 0;
  busy = false;
  rulingVisible = false;
  switchedSeat = null;
  el("dialogue").classList.remove("waiting");
  el("stop").disabled = true;
  el("start").disabled = false;
  el("dialogue-name").textContent = "COURT";
  el("dialogue-text").textContent = "Deliberation stopped.";
  try {
    await fetch("/stop", { method: "POST" });
  } finally {
    populateTranscriptSelect();
  }
});

// Replay: feed a saved transcript through the identical pipeline.
el("replay").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const events = JSON.parse(await file.text());
  resetRoom();
  for (const ev of events) enqueue(ev);
});

// Past deliberations: same replay pipeline, fetched from the server
// instead of a local file upload.
el("transcript-select").addEventListener("change", async (e) => {
  const id = e.target.value;
  if (!id) return;
  const events = await (await fetch(`/transcripts/${encodeURIComponent(id)}`)).json();
  resetRoom();
  for (const ev of events) enqueue(ev);
  e.target.value = "";
});

async function populateCaseSelect() {
  const { cases, default: def } = await (await fetch("/cases")).json();
  const select = el("case-select");
  select.innerHTML = "";
  for (const id of cases) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = id.replace(/_/g, " ");
    if (id === def) opt.selected = true;
    select.appendChild(opt);
  }
  loadFilm(select.value);
}

async function populateTranscriptSelect() {
  const { transcripts } = await (await fetch("/transcripts")).json();
  const select = el("transcript-select");
  select.innerHTML = '<option value="">— pick one —</option>';
  for (const id of transcripts) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = id;
    select.appendChild(opt);
  }
}

// On load, discover whether a deliberation is already running server-side
// (from an earlier click, another tab, or a stale reload) and rejoin it
// instead of leaving Start clickable into a dead-end "already-running" error.
async function rejoinIfRunning() {
  const { running } = await (await fetch("/status")).json();
  if (!running) return;
  el("start").disabled = true;
  el("stop").disabled = false;
  el("dialogue-name").textContent = "COURT";
  el("dialogue-text").textContent = "Rejoining deliberation in progress…";
  eventSource = new EventSource("/events");
  eventSource.onmessage = (msg) => enqueue(JSON.parse(msg.data));
}

renderSpeedLabel();
renderBoard();
populateCaseSelect();
populateTranscriptSelect();
rejoinIfRunning();
