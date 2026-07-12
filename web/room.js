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

function buildSeats() {
  const box = el("seats");
  box.innerHTML = "";
  for (const j of state.jurors) {
    const d = document.createElement("div");
    d.className = "seat";
    d.id = "seat-" + j.seat;
    d.dataset.vote = "unknown";
    d.innerHTML =
      `<div class="avatar">${j.seat}</div>` +
      `<div class="plate">Juror #${j.seat}<br>${j.occupation}</div>`;
    box.appendChild(d);
  }
}

function render() {
  if (state.jurors.length && !el("seat-" + state.jurors[0].seat)) buildSeats();
  for (const j of state.jurors) {
    const d = el("seat-" + j.seat);
    d.classList.toggle("active", state.activeSeat === j.seat);
    d.dataset.vote = state.votes[j.seat] || "unknown";
  }
  if (state.tally) {
    const t = el("tally");
    t.textContent =
      `GUILTY ${state.tally.guilty} — NOT GUILTY ${state.tally.not_guilty}`;
    t.classList.remove("bump");
    void t.offsetWidth;               // restart animation
    t.classList.add("bump");
  }
  if (state.verdict) {
    el("verdict-text").textContent = {
      guilty: "CONVICTED",
      not_guilty: "ACQUITTED",
      hung: "HUNG JURY",
    }[state.verdict.verdict];
    el("verdict-reason").textContent = state.verdict.reason || "";
    el("verdict").classList.add("show");
  }
  if (state.error) {
    el("dialogue-name").textContent = "ERROR";
    el("dialogue-text").textContent = state.error;
  }
}

function typewriter(text, done) {
  const box = el("dialogue-text");
  box.textContent = "";
  pendingFullText = text;
  let i = 0;
  typeTimer = setInterval(() => {
    box.textContent = text.slice(0, ++i);
    if (i >= text.length) {
      clearInterval(typeTimer);
      typeTimer = null;
      done();
    }
  }, Number(el("speed").value));
}

function showNext() {
  if (busy || pending.length === 0) return;
  const ev = pending.shift();
  state = applyEvent(state, ev);
  render();
  if (ev.type === "speech") {
    busy = true;
    el("dialogue-name").textContent = `Juror #${ev.seat} — ${ev.name}`;
    typewriter(ev.speech, () => {
      pauseTimer = setTimeout(() => {
        pauseTimer = null;
        busy = false;
        showNext();
      }, AUTO_ADVANCE_MS);
    });
  } else if (ev.type === "vote_called") {
    el("dialogue-name").textContent = "FOREMAN";
    el("dialogue-text").textContent = "Alright — let's take a vote.";
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

// Click: skip typewriter to full text, or skip the auto-advance pause.
el("dialogue").addEventListener("click", () => {
  if (typeTimer) {
    clearInterval(typeTimer);
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

function enqueue(ev) {
  pending.push(ev);
  showNext();
}

el("start").addEventListener("click", async () => {
  el("start").disabled = true;
  const source = new EventSource("/events");
  source.onmessage = (msg) => enqueue(JSON.parse(msg.data));
  await fetch("/start", { method: "POST" });
});

// Replay: feed a saved transcript through the identical pipeline.
el("replay").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const events = JSON.parse(await file.text());
  state = initialState();
  for (const ev of events) enqueue(ev);
});
