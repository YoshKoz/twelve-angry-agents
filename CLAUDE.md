# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Twelve Angry Men as a multi-agent LLM simulation: 12 juror agents plus a foreman deliberate a murder case to a verdict, rendered live in a visual-novel web UI with per-juror TTS voices.

## Commands

```bash
# Python tests (all / one file / one test)
pytest
pytest tests/test_orchestrator.py
pytest tests/test_orchestrator.py::test_name -v

# JS reducer test (node built-in runner, no npm deps)
node --test tests/test_room_state.mjs

# Run the app, then open http://localhost:8012 and POST /start (UI has a start button)
uvicorn server:app --port 8012
```

Live runs need a reachable Ollama instance (defaults to a LAN Windows desktop) — override with `OLLAMA_HOST` / `OLLAMA_MODEL` env vars — and `ELEVENLABS_API_KEY` for TTS.

## Architecture

Core design rule: the orchestrator contains no LLM logic. Agents are **injected callables**, so tests drive `Deliberation` with deterministic fakes and the live system injects real LLM adapters.

Event flow:

1. `orchestrator.py` — `Deliberation` state machine. Owns all state (transcript, private leans, tally, turn count), sequences turns, emits typed events (`case`, `roster`, `speaker`, `speech`, `vote_called`, `vote_result`, `prompt`, `reasoning`, `verdict`, `error`). Enforces rules in code, not prompts: verdict declarations require a unanimous ballot, no back-to-back votes, round-robin fallback when the foreman errors or returns invalid actions, turn cap → hung jury. Ballots run all 12 jurors concurrently via `ThreadPoolExecutor`. Every run writes the full event list to `transcripts/<run_id>.json`.
2. `live_agents.py` — glue adapting the injected-callable signatures (`juror_fn(card, case_text, transcript, mode)`, `foreman_fn(transcript, last_tally, turn, turn_cap)`) to real LLM calls. Attaches `_prompt`/`_raw_output` (trace panel) and TTS `audio` to replies.
3. `agent.py` — stateless Ollama HTTP wrapper. `ask_json`/`ask_json_detailed` extract and validate JSON from model output with schema-hint retries; raise `MalformedReply` after exhausting retries.
4. `server.py` — FastAPI. `POST /start` runs the deliberation in a background thread, bridging its synchronous `emit` into an asyncio queue via `loop.call_soon_threadsafe`; `GET /events` streams that queue as SSE. Single queue = one viewer per run (v1 limitation). Static `web/` mounted last so API routes win.
5. `web/room_state.js` — pure event reducer (no DOM), shared by the browser (`room.js`) and the node test. Keep it pure; DOM work belongs in `room.js`.

Supporting modules: `prompts.py` (all prompt text, no I/O — includes the HONESTY_RULE forbidding jurors from recognizing the film), `tally.py` (pure vote counting), `loader.py` (validates 12 juror cards from `data/jurors/`, seats exactly 1–12), `tts.py` (ElevenLabs, per-seat voice map, lru_cache on generated audio).

## Conventions

- Layering is strict: pure logic (`tally`, `prompts`, `room_state.js`) ← state machine (`orchestrator`) ← I/O adapters (`agent`, `tts`, `live_agents`) ← server. Don't import LLM/HTTP modules into the orchestrator or pure modules.
- New event types must be handled in `web/room_state.js` (its reducer throws on unknown types) and covered in `tests/test_room_state.mjs`.
- Juror cards in `data/jurors/juror_NN.json` require fields: id, seat, emoji, name, occupation, temperament, biases, speech_style (enforced by `loader.py`).
- Agent failures degrade gracefully, never crash the deliberation: a failing juror "passes", a failing foreman falls back to round-robin.
