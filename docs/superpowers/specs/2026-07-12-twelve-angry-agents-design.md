# Twelve Angry Agents — Design

**Date:** 2026-07-12
**Status:** Approved design, pre-implementation

## 1. Purpose

Re-enact *12 Angry Men* as a multi-agent simulation. Twelve AI jurors, each an
independent LLM agent with a private personality, deliberate a murder case. The
verdict is **emergent** — not scripted. A jury may acquit, convict, or hang. The
run is watched live in the browser as a **visual novel**: one active speaker at a
time, a dialogue box with typewriter text, and a live vote board.

Success = a run that plays out believably, where each juror's position shifts (or
doesn't) for reasons traceable to their card and the evidence — and it's fun to
watch.

## 2. Key Decisions (locked)

| Decision | Choice |
|---|---|
| Outcome | Emergent — verdict genuinely unknown at start |
| Interface | Web room, **visual-novel** presentation |
| Turn-taking | Foreman-moderated (a 13th agent picks the next speaker / calls votes) |
| Personas | The 12 film characters as personality seeds; **blind flips** — no juror is told the script or who "should" dissent |
| Case | The original *12 Angry Men* case, with an **honesty rule** forbidding use of outside/story knowledge |
| LLM engine | `claude -p` subprocess per agent (user has Claude Max, no API key — never use the SDK/API key) |
| Backend | Python + FastAPI, Server-Sent Events (SSE) to a static HTML/JS page |

## 3. Architecture

```
twelve-angry-agents/
├── orchestrator.py      # runs the deliberation loop, owns all state
├── agent.py             # wraps `claude -p`; one call = one agent turn
├── server.py            # FastAPI: serves room + streams SSE events
├── data/
│   ├── case_file.md     # shared evidence + testimony, seen by every juror
│   └── jurors/          # 12 personality cards (juror_01.json … juror_12.json)
├── web/
│   ├── room.html        # visual-novel stage
│   ├── room.css         # VN styling
│   └── room.js          # consumes SSE, drives the VN
├── transcripts/         # one JSON event-log per run (replayable)
└── docs/superpowers/specs/
```

Three roles, one loop:
- **Orchestrator** — pure Python. Holds transcript, private leans, vote tallies,
  turn counter. Decides nothing about content; only sequences calls and emits
  events. No LLM logic of its own.
- **agent.py** — one function: `ask(system_prompt, user_prompt) -> text` (and a
  JSON variant that validates structured output). Spawns `claude -p`, returns the
  reply. Stateless. Retries on malformed output.
- **server.py** — serves `web/`, exposes `GET /events` (SSE) and `POST /start`.
  Runs the orchestrator loop in a background task, pushing events onto a queue the
  SSE endpoint drains.

## 4. Agents

### Juror (×12)
- **Sees:** the shared case file + its own private card + the full transcript so
  far. Never sees other jurors' cards or private leans.
- **Card (`juror_NN.json`):** `{ id, seat, name, occupation, temperament, biases,
  speech_style }` — seeded from the film characters (angry-dad messenger owner,
  architect, bigoted garage owner, immigrant watchmaker, slum-raised orderly, …)
  but with **no** knowledge of the plot or of who dissents.
- **Output (structured JSON):** `{ speech: str, lean: "guilty"|"not_guilty"|
  "undecided", confidence: 0-1 }`. `speech` is what's shown in the VN; `lean` and
  `confidence` are private to the orchestrator until a vote is called.

### Foreman (×1, the 13th agent)
- **Sees:** transcript + current public tally + turn count.
- **Output (structured JSON):** exactly one action —
  `{ action: "call_on", target: N }` |
  `{ action: "call_vote" }` |
  `{ action: "declare", verdict: "guilty"|"not_guilty"|"hung", reason: str }`.
- Instructed to run a fair-but-realistic deliberation: open discussion, take
  periodic votes, keep order, declare only when a vote is unanimous or the room is
  genuinely deadlocked.

## 5. Deliberation Loop

1. Load case file + 12 cards. Emit `case` + `roster` events.
2. Foreman opens: forces an initial `call_vote` (the film's public 11–1 first
   vote — though here the split is emergent).
3. Repeat until the foreman `declare`s (or a hard turn cap, default 200, forces a
   `hung` safeguard):
   - Foreman turn → one action.
   - `call_on(N)` → juror N speaks; orchestrator records speech + updates that
     juror's private lean; emit `speaker` then `speech`.
   - `call_vote` → poll all 12 jurors **in parallel** for a public vote; tally;
     emit `vote_called` then `vote_result`.
   - `declare` → emit `verdict`, end.
4. Write the full event log to `transcripts/<timestamp>.json`.

Parallel voting keeps the expensive step fast; discussion turns are serial (they
must be — each juror reacts to the last).

## 6. State & Context

Each `claude -p` call is stateless, so the orchestrator passes the transcript-so-
far as context on every call. No hidden agent memory: a juror knows only what's
been said aloud plus its own card. Private `lean`/`confidence` are held by the
orchestrator and never shown to other agents or the UI between votes — only
aggregate public votes are revealed, mirroring the film's secret-ballot dynamic.

## 7. Visual-Novel UI

Single-screen stage, no scrolling required:

- **Background:** a stylized hot, cramped jury room.
- **Active speaker:** a portrait/avatar raised and lit; the other 11 dimmed.
  Portraits are placeholder stylized avatars to start (distinct color + seat
  number + name plate), swappable for richer art later.
- **Name plate:** `Juror #N — <occupation>`.
- **Dialogue box:** bottom, VN-style, with typewriter text reveal. Click (or a
  key) skips the typewriter to full text; auto-advances after a readable pause.
- **Vote board:** twelve seat tiles around/along the table edge, colored
  **guilty = red**, **not-guilty = green**, **undecided/unknown = grey**. Updates
  only on a `vote_result` (between votes it shows the last known public vote).
- **Tally banner:** `GUILTY n — NOT GUILTY m`, animates on each vote.
- **Verdict card:** full-screen overlay on `verdict` (acquitted / convicted /
  hung).
- **Controls:** Start, auto-play speed slider, click-to-advance. A run can be
  replayed by feeding a saved transcript JSON through the same renderer.

The UI is a **pure renderer** of the SSE event stream — it holds no simulation
logic. The event log *is* the transcript, so live view and replay use identical
code.

## 8. Honesty Rule

Every agent system prompt ends with:

> "Reason ONLY from the evidence and statements in this room. Do NOT use any
> outside knowledge, and do NOT draw on recognition of any book, film, or story.
> You are this person, deciding this case now, for the first time."

Accepted as a soft guard: the underlying model recognizes the source material, so
leakage is possible. This is a known, accepted limitation of the "original case"
choice (vs. reskinning).

## 9. Error Handling

- **Malformed agent JSON** → agent.py retries the call up to 3× with a
  "return ONLY valid JSON matching this schema" reminder; on repeated failure the
  orchestrator logs it and, for a juror, treats the turn as a pass; for the
  foreman, falls back to round-robin `call_on` so the loop never wedges.
- **`claude -p` nonzero exit / timeout** → capture stderr, retry once, then abort
  the run with a clear error event to the UI (never silently `|| true`).
- **Turn cap reached** → foreman forced to `declare hung`; run ends cleanly.
- **No infinite votes** → orchestrator rejects two `call_vote`s with no
  intervening speech (forces discussion between ballots).

## 10. Testing

- **agent.py** — mock the `claude -p` subprocess; assert prompt assembly, JSON
  parsing, retry-on-malformed, timeout handling. No real LLM calls in unit tests.
- **orchestrator loop** — inject a scripted fake agent (deterministic replies) to
  drive a full deliberation to each terminal state (unanimous acquit, unanimous
  convict, hung, turn-cap). Assert emitted event sequence + transcript shape.
- **Vote/tally logic** — pure functions, unit-tested directly.
- **UI** — smoke test: replay a canned transcript JSON, assert each event type
  renders without error.
- **One live end-to-end** — a single real run (manual), to confirm real
  `claude -p` output parses and the room plays through.

## 11. Cost / Performance Note

Foreman-moderated = 2 serial `claude -p` calls per discussion turn, plus 12
parallel calls per vote. Each CLI spawn is ~seconds, so a full run is minutes and
many subprocess spawns — acceptable on Claude Max. A later "fast mode" toggle
(round-robin, skip the foreman call) is out of scope for v1 but noted as an easy
extension.

## 12. Out of Scope (v1)

- Reskinned/generated alternate cases (original case only for v1).
- Rich AI-generated portrait art (placeholder avatars for v1).
- Multi-run batch/statistics harness.
- Fast (round-robin) mode toggle.
