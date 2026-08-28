"""Integration tests for the FastAPI event server. LLM and TTS calls are
patched out so these run fully offline — all five live agent hooks are stubbed
in the fixture, so no test can fall through to a real backend."""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import server


def _accepting_judge(*_args):
    return {"accept": True, "instruction": ""}


def _silent_bailiff(*_args):
    return {"granted": False, "record": "", "possible": False, "result": ""}


def _fast_assessor(card, _case, _exhibit, _findings=None):
    return {"position": "raises_doubt", "reasoning": f"seat {card['seat']}",
            "confidence": 0.5}


def _fast_juror(card, _case, _transcript, mode, *_args):
    if mode == "vote":
        return {"vote": "guilty"}
    return {"speech": f"J{card['seat']} speaks.", "lean": "guilty",
            "confidence": 0.9}


def _slow_foreman_then_hang(*_args):
    time.sleep(0.05)
    return {"action": "call_on", "target": 8}


def _declare_hung(*_args):
    return {"action": "declare", "verdict": "hung", "reason": "x"}


@pytest.fixture
def client():
    # every agent the server can reach is stubbed here, so no test can fall
    # through to a real backend; individual tests re-patch what they drive
    with patch("server.tts.narrate", return_value=None), \
         patch("tts.narrate", return_value=None), \
         patch("server.live_assess_fn", _fast_assessor), \
         patch("server.live_juror_fn", _fast_juror), \
         patch("server.live_foreman_fn", _declare_hung), \
         patch("server.live_judge_fn", _accepting_judge), \
         patch("server.live_bailiff_fn", _silent_bailiff):
        with TestClient(server.app) as c:
            yield c
    # tests share the module-level run state; reset between tests
    server._running.clear()
    server._stop_requested.clear()
    server._history.clear()


def _wait_for_idle(limit=400):
    for _ in range(limit):
        if not server._running.is_set():
            return True
        time.sleep(0.02)
    return False


# --- case selection and lifecycle -----------------------------------------

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


def test_start_selects_requested_case_and_streams_case_and_docket(client):
    assert client.post("/start", params={"case_id": "pier7_arson"}
                       ).json()["status"] == "started"
    assert _wait_for_idle()
    types = [e["type"] for e in server._history]
    case_event = next(e for e in server._history if e["type"] == "case")
    assert "arson" in case_event["text"].lower()
    assert case_event["title"] and case_event["charge"]
    docket = next(e for e in server._history if e["type"] == "docket")
    assert docket["exhibits"] and all(
        {"id", "name", "claim"} == set(e) for e in docket["exhibits"])
    assert "verdict" in types


def test_start_puts_the_first_exhibit_and_all_twelve_reads_on_the_wire(client):
    with patch("server.live_foreman_fn", _slow_foreman_then_hang):
        client.post("/start")
        for _ in range(400):
            if any(e["type"] == "positions" for e in server._history):
                break
            time.sleep(0.02)
        client.post("/stop")
        assert _wait_for_idle()
    assert len([e for e in server._history if e["type"] == "assessed"]) == 12
    positions = next(e for e in server._history if e["type"] == "positions")
    assert set(positions["positions"].values()) == {"raises_doubt"}


def test_start_refuses_a_second_concurrent_run(client):
    with patch("server.live_foreman_fn", _slow_foreman_then_hang):
        client.post("/start")
        assert client.post("/start").json()["status"] == "already-running"
        client.post("/stop")
        assert _wait_for_idle()


def test_stop_ends_a_running_deliberation(client):
    with patch("server.live_foreman_fn", _slow_foreman_then_hang):
        assert client.post("/start").json()["status"] == "started"
        time.sleep(0.1)          # let a discussion turn or two happen
        assert client.post("/stop").json()["status"] == "stopping"
        assert _wait_for_idle()
        verdict = next(e for e in server._history if e["type"] == "verdict")
        assert verdict["verdict"] == "stopped"


def test_status_reflects_running_state(client):
    assert client.get("/status").json() == {"running": False}
    with patch("server.live_foreman_fn", _slow_foreman_then_hang):
        client.post("/start")
        assert client.get("/status").json() == {"running": True}
        client.post("/stop")
        assert _wait_for_idle()
        assert client.get("/status").json() == {"running": False}


# --- the full court is seated ---------------------------------------------

def test_start_seats_every_agent_the_room_needs(client):
    """Without these the room is degraded: nobody reads the exhibits, nobody
    can answer the jury and nobody can refuse a verdict."""
    import live_agents

    fake = MagicMock()
    with patch("server.live_assess_fn", live_agents.live_assess_fn), \
         patch("server.live_judge_fn", live_agents.live_judge_fn), \
         patch("server.live_bailiff_fn", live_agents.live_bailiff_fn), \
         patch("server.Deliberation", return_value=fake) as delib:
        client.post("/start")
        for _ in range(400):
            if delib.called and fake.run.called:
                break
            time.sleep(0.02)
    args, kwargs = delib.call_args
    assert args[0]["id"] == "the_stabbing"      # the case dict, not its text
    assert args[0]["exhibits"]
    assert args[2] is _fast_juror
    assert args[3] is _declare_hung
    assert kwargs["bailiff_fn"] is live_agents.live_bailiff_fn
    assert kwargs["judge_fn"] is live_agents.live_judge_fn
    assert kwargs["assess_fn"] is live_agents.live_assess_fn
    assert kwargs["should_stop"] == server._stop_requested.is_set
    fake.run.assert_called_once()


def test_the_case_handed_to_the_room_carries_no_film_finding(client):
    """The one text that must never reach an agent travels on its own
    endpoint, not inside the case."""
    import loader

    fake = MagicMock()
    with patch("server.Deliberation", return_value=fake) as delib:
        client.post("/start", params={"case_id": "the_stabbing"})
        for _ in range(400):
            if delib.called and fake.run.called:
                break
            time.sleep(0.02)
    case = delib.call_args[0][0]
    assert all("film_finding" not in ex for ex in case["exhibits"])
    blob = case["narrative"] + repr(case["exhibits"])
    for finding in loader.load_film_findings("the_stabbing").values():
        assert finding not in blob


def test_a_crashing_deliberation_surfaces_as_an_error_event(client):
    fake = MagicMock()
    fake.run.side_effect = RuntimeError("boom")
    with patch("server.Deliberation", return_value=fake):
        client.post("/start")
        assert _wait_for_idle()
    error = next(e for e in server._history if e["type"] == "error")
    assert "RuntimeError: boom" in error["message"]


# --- what the film concluded ----------------------------------------------

def test_film_endpoint_serves_the_comparison_column(client):
    import loader

    resp = client.get("/film/the_stabbing")
    assert resp.status_code == 200
    findings = resp.json()["findings"]
    assert findings
    docket_ids = {ex["id"] for ex in
                  loader.load_case("the_stabbing")["exhibits"]}
    assert set(findings) <= docket_ids     # keyed by exhibit, for the UI
    assert all(isinstance(v, str) and v for v in findings.values())


def test_film_findings_are_not_in_the_case_the_agents_get(client):
    import loader

    findings = client.get("/film/the_stabbing").json()["findings"]
    case = loader.load_case("the_stabbing")
    for exhibit_id, text in findings.items():
        assert text not in case["narrative"], exhibit_id
        assert all(text not in str(ex.values()) for ex in case["exhibits"])


def test_film_endpoint_is_empty_for_a_case_the_film_never_covered(client):
    assert client.get("/film/pier7_arson").json() == {"findings": {}}


def test_film_endpoint_is_empty_rather_than_404_for_an_unknown_case(client):
    assert client.get("/film/no_such_case").json() == {"findings": {}}


@pytest.mark.parametrize("bad_id", ["..%2F..%2Fetc%2Fpasswd", "a%2Fb",
                                    "with%20space", "%00"])
def test_film_endpoint_rejects_an_id_that_is_not_an_id(client, bad_id):
    resp = client.get(f"/film/{bad_id}")
    assert resp.status_code in (400, 404)


def test_film_endpoint_is_guarded_like_the_transcript_endpoint(client):
    """Same id shape, same guard — one regex, no second-best copy of it."""
    assert server._TRANSCRIPT_ID.match("the_stabbing")
    assert not server._TRANSCRIPT_ID.match("../etc/passwd")
    assert client.get("/film/..%2Fetc%2Fpasswd").status_code in (400, 404)


# --- narration -------------------------------------------------------------

def test_the_case_reading_and_vote_call_are_narrated():
    with patch("server.tts.narrate", side_effect=lambda t: f"audio:{t}"):
        case = server._attach_narration({"type": "case", "text": "the file"})
        called = server._attach_narration({"type": "vote_called",
                                           "method": "hands"})
    assert case["audio"] == "audio:the file"
    assert called["audio"] == f"audio:{server.VOTE_CALL_LINE}"


def test_what_the_court_reads_in_is_spoken_by_the_room_not_a_juror():
    with patch("server.tts.narrate", side_effect=lambda t: f"audio:{t}"):
        ev = server._attach_narration({"type": "record", "kind": "evidence",
                                       "seat": 8, "available": True,
                                       "text": "It is a switchblade."})
    assert ev["audio"] == "audio:It is a switchblade."


def test_the_judges_instruction_is_narrated():
    with patch("server.tts.narrate", side_effect=lambda t: f"audio:{t}"):
        ev = server._attach_narration({"type": "judge_ruling",
                                       "accepted": False,
                                       "instruction": "Go back and deliberate."})
    assert ev["audio"] == "audio:Go back and deliberate."


def test_a_silent_judge_ruling_gets_no_audio():
    with patch("server.tts.narrate", side_effect=lambda t: f"audio:{t}"):
        ev = server._attach_narration({"type": "judge_ruling",
                                       "accepted": True, "instruction": ""})
    assert "audio" not in ev


def test_events_without_narration_are_passed_through_untouched():
    with patch("server.tts.narrate", side_effect=AssertionError("no TTS")):
        for ev in ({"type": "speech", "seat": 1, "speech": "hi"},
                   {"type": "ruling", "seat": 1, "granted": True},
                   {"type": "vote_change", "seat": 1, "vote": "guilty"},
                   {"type": "docket", "exhibits": []},
                   {"type": "exhibit", "id": "x", "record": "long text"},
                   {"type": "assessed", "seat": 1, "exhibit": "x"},
                   {"type": "positions", "exhibit": "x", "positions": {}},
                   {"type": "exhibit_closed", "id": "x", "finding": "f"},
                   {"type": "docket_closed", "findings": []},
                   {"type": "verdict_announced", "verdict": "hung"}):
            assert "audio" not in server._attach_narration(ev)


def test_a_narration_failure_never_drops_the_event():
    with patch("server.tts.narrate", side_effect=RuntimeError("TTS down")):
        ev = server._attach_narration({"type": "case", "text": "the file"})
    assert ev == {"type": "case", "text": "the file"}


def test_narration_prewarm_reads_the_narrative_not_the_case_dict():
    spoken = []
    with patch("server.tts.narrate", side_effect=spoken.append), \
         patch("tts.narrate", side_effect=spoken.append):
        with TestClient(server.app):
            for _ in range(200):
                if len(spoken) > len(server.list_cases()):
                    break
                time.sleep(0.02)
    assert all(isinstance(t, str) for t in spoken)
    assert any("knife" in t.lower() for t in spoken)
    assert server.VOTE_CALL_LINE in spoken
    server._running.clear()
    server._history.clear()


# --- SSE -------------------------------------------------------------------

def _make_request(headers=None):
    """Minimal ASGI Request for calling the /events route directly.

    Avoids driving the (intentionally infinite) SSE generator through
    TestClient's synchronous streaming, which never returns and hangs."""
    from starlette.requests import Request
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": hdrs})


def test_events_replays_history_to_new_subscriber(client):
    client.post("/start")
    assert _wait_for_idle()

    async def _read_first_n(n, headers=None):
        resp = await server.events(_make_request(headers))
        it = resp.body_iterator.__aiter__()  # type: ignore[attr-defined]
        try:
            return [str(await asyncio.wait_for(anext(it), timeout=1))
                    for _ in range(n)]
        finally:
            await it.aclose()  # type: ignore[attr-defined]

    chunks = asyncio.run(_read_first_n(3))
    assert "id: 0" in chunks[0]
    assert '"type": "case"' in chunks[0]
    assert "id: 1" in chunks[1]
    assert '"type": "roster"' in chunks[1]
    assert "id: 2" in chunks[2]
    assert '"type": "docket"' in chunks[2]

    # Last-Event-ID lets a reconnecting client skip what it already saw
    chunk = asyncio.run(_read_first_n(1, {"last-event-id": "1"}))[0]
    assert "id: 2" in chunk
    assert '"type": "docket"' in chunk


# --- transcripts -----------------------------------------------------------

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
