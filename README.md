# Twelve Angry Agents

Twelve LLM jurors and a foreman deliberate a murder case to verdict, live, in a visual-novel web UI with per-juror TTS narration.

## What this is

Each juror is a small persona (occupation, temperament, biases, speech style) driving an Ollama-backed agent. A foreman agent calls on jurors to speak, calls for votes, and pushes the room toward a unanimous verdict — or a hung jury if it can't get there. The whole thing streams to the browser over SSE as a courtroom scene: portraits, speech bubbles, a vote tally, and narrated audio.

The interesting part isn't the UI, it's the state machine underneath: `orchestrator.py` owns all deliberation state and enforces the rules in code rather than in prompts (no back-to-back votes, verdicts require unanimity, turn cap forces a hung jury). Agents are injected callables, so the whole thing is unit-tested against deterministic fakes with no LLM in the loop.

## Features

- 12 juror agents with distinct cards (`data/jurors/*.json`) — occupation, temperament, biases, speech style
- Foreman agent sequences turns, calls votes, and falls back to round-robin if it errors or stalls
- Deliberation rules enforced in code: unanimous verdict required, no consecutive votes, forced re-vote every 15 turns, turn cap → hung jury, graceful degradation on agent failure
- Full transcript + event log written to `transcripts/<run_id>.json` per run
- Visual-novel web UI (`web/`) driven by a pure event-reducer (`room_state.js`), shared between the browser and its own test suite
- Optional per-juror ElevenLabs TTS narration, pre-warmed at server startup and cached
- Two included cases (`data/cases/`)

## Architecture

```
orchestrator.py   Deliberation state machine — pure logic, no I/O, no LLM calls
live_agents.py    adapts injected-callable signatures to real Ollama + TTS calls
agent.py          stateless Ollama HTTP client (JSON-mode, schema-hint retries)
prompts.py        all prompt text, no I/O
tally.py          pure vote counting
loader.py         validates and loads the 12 juror cards
tts.py            ElevenLabs narration, per-seat voice map, cached
server.py         FastAPI — POST /start runs a deliberation in a background
                  thread, GET /events streams it as SSE
web/room_state.js pure event reducer shared by the browser and node tests
web/room.js       DOM rendering on top of the reducer
```

Layering is strict: pure logic → state machine → I/O adapters → server. The orchestrator never imports an LLM or HTTP module, which is what makes it testable with fakes.

## Tech stack

Python (FastAPI, httpx, uvicorn) on the backend, vanilla JS/HTML/CSS on the frontend, [Ollama](https://ollama.com) for inference, [ElevenLabs](https://elevenlabs.io) for TTS. Tested with `pytest` (backend, ~8 test files) and Node's built-in test runner (`node --test`) for the JS reducer.

## Running it

Needs a reachable Ollama instance (defaults to a LAN host, override via env) and, optionally, an ElevenLabs key for narration — without it the room still runs, just silently.

```bash
pip install -r requirements.txt
cp .env.example .env   # set OLLAMA_HOST / OLLAMA_MODEL / ELEVENLABS_API_KEY

uvicorn server:app --port 8012
# open http://localhost:8012 and click Start
```

## Tests

```bash
pytest                              # full backend suite
pytest tests/test_orchestrator.py   # one file
node --test tests/test_room_state.mjs
```

## Status

Single-viewer per run (the SSE fan-out uses one queue) — fine for a demo, not built for concurrent audiences. Everything else described above is implemented and covered by tests, not aspirational.
