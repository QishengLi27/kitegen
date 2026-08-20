"""Tests for demo.backend helpers, especially the /chat SSE stream."""

import asyncio
import json

import kitegen as kg
from demo.backend import Session, _chat_event_stream, update_paper_config


class _FakeRequest:
    """Minimal stand-in for a FastAPI Request (json() only)."""

    def __init__(self, data):
        self._data = data

    async def json(self):
        return self._data


class _FakePipeline:
    """A pipeline that yields a scripted sequence of events."""

    def __init__(self, events):
        self._events = events

    async def invoke_stream(self, state, thread_id):
        for event in self._events:
            yield event


class _FakeSaver:
    def __init__(self, final_state=None):
        self._final_state = final_state or {}

    async def load(self, thread_id):
        return self._final_state


def _parse_sse(lines):
    """Yield parsed JSON payloads from SSE data: lines."""
    for line in lines:
        if line.startswith("data: "):
            yield json.loads(line[6:])


def _run_stream(stream):
    """Collect an async generator into a list synchronously."""
    return asyncio.run(_collect(stream))


async def _collect(stream):
    return [line async for line in stream]


def test_progress_events_emitted_for_tool_calls():
    events = [
        kg.NodeStart(node="research"),
        kg.ToolCallEvent(node="stock researcher", tool="lookup_stock", arguments={"symbol": "TSLA"}),
        kg.ToolResultEvent(node="stock researcher", tool="lookup_stock", result="price 350"),
        kg.NodeEnd(node="research"),
        kg.Complete(),
    ]

    session = Session()
    stream = _chat_event_stream(
        pipeline=_FakePipeline(events),
        pipeline_saver=_FakeSaver(),
        state={},
        thread_id="t1",
        session=session,
        message="Tesla",
        symbol="TSLA",
        stage_labels={"research": "Research"},
    )

    lines = _run_stream(stream)
    payloads = list(_parse_sse(lines))

    assert payloads[0]["type"] == "stage_started"
    assert payloads[0]["stage"] == "research"

    progress_calling = payloads[1]
    assert progress_calling["type"] == "progress"
    assert progress_calling["stage"] == "research"
    assert progress_calling["status"] == "calling"
    assert progress_calling["tool"] == "lookup_stock"
    assert "Calling lookup_stock" in progress_calling["detail"]

    progress_done = payloads[2]
    assert progress_done["type"] == "progress"
    assert progress_done["status"] == "done"
    assert progress_done["tool"] == "lookup_stock"
    assert "returned" in progress_done["detail"]

    assert payloads[3]["type"] == "stage_done"
    assert payloads[-1]["type"] == "done"


def test_final_advice_streamed_as_tokens():
    events = [
        kg.NodeStart(node="advice"),
        kg.NodeEnd(node="advice"),
        kg.Complete(),
    ]

    session = Session()
    stream = _chat_event_stream(
        pipeline=_FakePipeline(events),
        pipeline_saver=_FakeSaver({"output": "Buy TSLA."}),
        state={},
        thread_id="t1",
        session=session,
        message="Tesla",
        symbol="TSLA",
        stage_labels={"advice": "Advice"},
    )

    lines = _run_stream(stream)
    payloads = list(_parse_sse(lines))

    token_payloads = [p for p in payloads if p["type"] == "token"]
    assert "".join(p["content"] for p in token_payloads) == "Buy TSLA."
    assert session.history == [
        {"role": "user", "content": "Tesla"},
        {"role": "assistant", "content": "Buy TSLA."},
    ]


def test_node_error_becomes_error_event():
    events = [
        kg.NodeStart(node="research"),
        kg.NodeError(node="research", error="something failed"),
    ]

    stream = _chat_event_stream(
        pipeline=_FakePipeline(events),
        pipeline_saver=_FakeSaver(),
        state={},
        thread_id="t1",
        session=Session(),
        message="Tesla",
        symbol="TSLA",
        stage_labels={"research": "Research"},
    )

    lines = _run_stream(stream)
    payloads = list(_parse_sse(lines))

    assert any(p["type"] == "error" and "research: something failed" in p["message"] for p in payloads)


# ── /paper/config: risk_mode handling ────────────────────────────────────────


def _run_coro(coro):
    """Await a single coroutine synchronously."""
    return asyncio.run(coro)


def test_paper_config_accepts_risk_mode(monkeypatch, tmp_path):
    monkeypatch.setattr("demo.paper.DATA_DIR", tmp_path)
    from demo.paper import load_config

    resp = _run_coro(update_paper_config(_FakeRequest({"risk_mode": "aggressive"})))

    assert resp["status"] == "ok"
    saved = load_config()
    assert saved.risk_mode == "aggressive"
    # Mode change adopts the mode's profile defaults for derived fields
    assert saved.max_position_pct == 0.80
    assert saved.stop_loss_pct == 0.50


def test_paper_config_explicit_values_beat_mode_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr("demo.paper.DATA_DIR", tmp_path)
    from demo.paper import load_config

    resp = _run_coro(update_paper_config(_FakeRequest({
        "risk_mode": "aggressive",
        "max_position_pct": 0.15,
    })))
    assert resp["status"] == "ok"

    saved = load_config()
    assert saved.risk_mode == "aggressive"
    assert saved.max_position_pct == 0.15
    assert saved.stop_loss_pct == 0.50  # other derived field follows the profile


def test_paper_config_rejects_unknown_mode(monkeypatch, tmp_path):
    monkeypatch.setattr("demo.paper.DATA_DIR", tmp_path)

    resp = _run_coro(update_paper_config(_FakeRequest({"risk_mode": "yolo"})))
    assert resp["status"] == "error"
    assert "risk_mode" in resp["message"]
