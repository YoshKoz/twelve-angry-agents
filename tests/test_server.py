"""Integration tests for the FastAPI event server. LLM and TTS calls are
patched out so these run fully offline."""

import asyncio
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture
def client():
    with patch("server.tts.narrate", return_value=None), \
         patch("tts.narrate", return_value=None):
        with TestClient(server.app) as c:
            yield c
    # tests share the module-level run state; reset between tests
    server._running.clear()
    server._stop_requested.clear()
    server._history.clear()


def _fast_juror(card, _case_text, _transcript, mode):
    if mode == "vote":
        return {"vote": "guilty"}
    return {"speech": f"J{card['seat']} speaks.", "lean": "guilty",
            "confidence": 0.9}


def _slow_foreman_then_hang(_transcript, _last_tally, _turn, _turn_cap):
    time.sleep(0.05)
    return {"action": "call_on", "target": 8}


def _split_juror(card, _case_text, _transcript, mode):
    # not unanimous, so the opening ballot doesn't end the run immediately
    if mode == "vote":
        return {"vote": "guilty" if card["seat"] != 1 else "not_guilty"}
    return {"speech": f"J{card['seat']} speaks.", "lean": "guilty",
            "confidence": 0.9}


def _declare_hung(*_args):
    return {"action": "declare", "verdict": "hung", "reason": "x"}


def test_cases_endpoint_lists_available_cases(client):
    resp = client.get("/cases")
    assert resp.status_code == 200
    body = resp.json()
    assert "the_stabbing" in body["cases"]
    assert "pier7_arson" in body["cases"]
    assert body["default"] == "the_stabbing"


def test_start_rejects_unknown_case_id(client):
    resp = client.post("/start", params={"case_id": "no_such_case"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"
    assert not server._running.is_set()


def test_stop_when_nothing_running(client):
    resp = client.post("/stop")
    assert resp.json()["status"] == "not-running"


def test_start_selects_requested_case_and_streams_case_event(client):
    with patch("server.live_juror_fn", _fast_juror), \
         patch("server.live_foreman_fn", _declare_hung):
        resp = client.post("/start", params={"case_id": "pier7_arson"})
        assert resp.json()["status"] == "started"
        for _ in range(200):
            if not server._running.is_set() and server._history:
                break
            time.sleep(0.02)
        types = [e["type"] for e in server._history]
        case_event = next(e for e in server._history if e["type"] == "case")
        assert "arson" in case_event["text"].lower()
        assert "verdict" in types


def test_stop_ends_a_running_deliberation(client):
    with patch("server.live_juror_fn", _split_juror), \
         patch("server.live_foreman_fn", _slow_foreman_then_hang):
        resp = client.post("/start")
        assert resp.json()["status"] == "started"
        time.sleep(0.1)          # let it get past the opening ballot
        stop_resp = client.post("/stop")
        assert stop_resp.json()["status"] == "stopping"
        for _ in range(200):
            if not server._running.is_set():
                break
            time.sleep(0.02)
        assert not server._running.is_set()
        verdict = next(e for e in server._history if e["type"] == "verdict")
        assert verdict["verdict"] == "stopped"


def _make_request(headers=None):
    """Minimal ASGI Request for calling the /events route directly.

    Avoids driving the (intentionally infinite) SSE generator through
    TestClient's synchronous streaming, which never returns and hangs."""
    from starlette.requests import Request
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": hdrs})


def test_events_replays_history_to_new_subscriber(client):
    with patch("server.live_juror_fn", _fast_juror), \
         patch("server.live_foreman_fn", _declare_hung):
        client.post("/start")
        for _ in range(200):
            if not server._running.is_set():
                break
            time.sleep(0.02)

    async def _read_first_n(n, headers=None):
        resp = await server.events(_make_request(headers))
        it = resp.body_iterator.__aiter__()  # type: ignore[attr-defined]
        try:
            return [str(await asyncio.wait_for(anext(it), timeout=1))
                    for _ in range(n)]
        finally:
            await it.aclose()  # type: ignore[attr-defined]

    chunks = asyncio.run(_read_first_n(2))
    assert "id: 0" in chunks[0]
    assert "\"type\": \"case\"" in chunks[0]
    assert "id: 1" in chunks[1]
    assert "\"type\": \"roster\"" in chunks[1]

    # Last-Event-ID lets a reconnecting client skip what it already saw
    chunk = asyncio.run(_read_first_n(1, {"last-event-id": "0"}))[0]
    assert "id: 1" in chunk
    assert "\"type\": \"roster\"" in chunk


def test_transcripts_endpoint_lists_saved_runs(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", tmp_path)
    (tmp_path / "20260101-000000.json").write_text('[{"type": "case"}]')
    (tmp_path / "20260102-000000.json").write_text('[{"type": "case"}]')
    resp = client.get("/transcripts")
    assert resp.json()["transcripts"] == ["20260102-000000", "20260101-000000"]


def test_transcript_serves_saved_content(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", tmp_path)
    (tmp_path / "run1.json").write_text('[{"type": "case", "text": "hi"}]')
    resp = client.get("/transcripts/run1")
    assert resp.json() == [{"type": "case", "text": "hi"}]


def test_transcript_rejects_path_traversal(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", tmp_path)
    resp = client.get("/transcripts/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code in (400, 404)


def test_transcript_404_for_unknown_id(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", tmp_path)
    resp = client.get("/transcripts/no_such_run")
    assert resp.status_code == 404


def test_status_reflects_running_state(client):
    assert client.get("/status").json() == {"running": False}
    with patch("server.live_juror_fn", _split_juror), \
         patch("server.live_foreman_fn", _slow_foreman_then_hang):
        client.post("/start")
        assert client.get("/status").json() == {"running": True}
        client.post("/stop")
        for _ in range(200):
            if not server._running.is_set():
                break
            time.sleep(0.02)
        assert client.get("/status").json() == {"running": False}
