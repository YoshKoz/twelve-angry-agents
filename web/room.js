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

const el = (id) => document.getElementById(id);

function endRun() {
  if (eventSource) { eventSource.close(); eventSource = null; }
  el("start").disabled = false;
  el("stop").disabled = true;
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
  }
  if (state.tally) {
    const t = el("tally");
    t.textContent =
      `GUILTY ${state.tally.guilty} — NOT GUILTY ${state.tally.not_guilty}`;
    t.classList.remove("bump");
    void t.offsetWidth;               // restart animation
    t.classList.add("bump");
    // ballot slips: guilty fills from the left, acquittal from the right
    const segs = el("tally-bar").children;
    for (let i = 0; i < segs.length; i++) {
      segs[i].className = "seg " +
        (i < state.tally.guilty ? "seg-guilty"
         : i >= segs.length - state.tally.not_guilty ? "seg-acquit"
         : "seg-open");
    }
  }
  if (state.verdict) {
    el("verdict-text").textContent = {
      guilty: "CONVICTED",
      not_guilty: "ACQUITTED",
      hung: "HUNG JURY",
      stopped: "DELIBERATION STOPPED",
      aborted: "DELIBERATION ABORTED",
    }[state.verdict.verdict] || state.verdict.verdict.toUpperCase();
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
  renderSidePanel();
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
}

function typewriter(text, done) {
  const box = el("dialogue-text");
  box.textContent = "";
  pendingFullText = text;
  let i = 0;
  // setTimeout (not setInterval) so the speed slider is re-read every
  // character — dragging it mid-message takes effect immediately, not
  // just on the next speech.
  function tick() {
    box.textContent = text.slice(0, ++i);
    if (i >= text.length) {
      typeTimer = null;
      done();
      return;
    }
    typeTimer = setTimeout(tick, Number(el("speed").value));
  }
  typeTimer = setTimeout(tick, Number(el("speed").value));
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

function showNext() {
  if (busy || pending.length === 0) return;
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
      }, AUTO_ADVANCE_MS);
    });
  } else if (ev.type === "case") {
    // clerk reads the case aloud; queue waits, click dialogue to skip
    el("dialogue-name").textContent = "CLERK OF THE COURT";
    el("dialogue-text").textContent =
      "The clerk reads the case into the record… (click to skip)";
    if (ev.audio) {
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
  } else if (ev.type === "vote_called") {
    el("dialogue-name").textContent = "FOREMAN";
    el("dialogue-text").textContent = "Alright — let's take a vote.";
    if (ev.audio) playAudio(ev.audio);
    busy = true;
    pauseTimer = setTimeout(() => {
      pauseTimer = null;
      busy = false;
      showNext();
    }, AUTO_ADVANCE_MS / 2);
  } else {
    showNext();
  }
}

// Click: skip clerk reading, skip typewriter to full text,
// or skip the auto-advance pause.
el("dialogue").addEventListener("click", () => {
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
    }, AUTO_ADVANCE_MS);
  } else if (pauseTimer) {
    clearTimeout(pauseTimer);
    pauseTimer = null;
    busy = false;
    showNext();
  }
});

// Case file collapse toggle.
el("case-toggle").addEventListener("click", () => {
  el("case-panel").classList.toggle("collapsed");
});

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

function resetRoom() {
  if (eventSource) { eventSource.close(); eventSource = null; }
  stopAllAudio();
  if (typeTimer) { clearTimeout(typeTimer); typeTimer = null; }
  if (pauseTimer) { clearTimeout(pauseTimer); pauseTimer = null; }
  pending.length = 0;
  busy = false;
  state = initialState();
  el("verdict").classList.remove("show");
}

el("start").addEventListener("click", async () => {
  resetRoom();
  el("start").disabled = true;
  el("stop").disabled = false;
  el("dialogue-name").textContent = "CLERK OF THE COURT";
  el("dialogue-text").textContent = "Court is convening…";
  const caseId = el("case-select").value;
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

el("stop").addEventListener("click", async () => {
  // stop playback immediately; the server winds the run down between turns
  stopAllAudio();
  if (typeTimer) { clearTimeout(typeTimer); typeTimer = null; }
  if (pauseTimer) { clearTimeout(pauseTimer); pauseTimer = null; }
  pending.length = 0;
  busy = false;
  el("stop").disabled = true;
  el("dialogue-name").textContent = "COURT";
  el("dialogue-text").textContent = "Stopping deliberation…";
  await fetch("/stop", { method: "POST" });
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

populateCaseSelect();
populateTranscriptSelect();
rejoinIfRunning();
