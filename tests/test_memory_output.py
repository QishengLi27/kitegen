"""Tests for kitegen.memory and Agent structured output."""

from dataclasses import dataclass

import pytest

import kitegen as kg
from tests.test_agent import FakeLLM, make_response


async def test_buffer_memory_roundtrip():
    mem = kg.BufferMemory(size=4)
    mem.add("user", "Q1")
    mem.add("assistant", "A1")
    mem.add("user", "Q2")
    mem.add("assistant", "A2")
    mem.add("user", "Q3")
    mem.add("assistant", "A3")

    msgs = mem.get()
    assert len(msgs) == 4  # capped
    assert msgs[0].content == "Q2"  # oldest evicted


async def test_agent_injects_memory_into_prompt():
    mem = kg.BufferMemory(size=4)
    mem.add("user", "previous question")
    mem.add("assistant", "previous answer")

    llm = FakeLLM([make_response(content="ok")])
    agent = kg.Agent(role="test", goal="test", llm=llm, memory=mem)

    await agent.run({"input": "current question"})

    sent = llm.calls[0][0]
    roles = [m.role for m in sent]
    assert roles == ["system", "user", "assistant", "user"]
    assert sent[1].content == "previous question"
    assert sent[3].content == "current question"

    # Exchange recorded after the run
    assert len(mem) == 4
    assert mem.get()[-1].content == "ok"


async def test_agent_output_schema_dataclass():
    @dataclass
    class Decision:
        action: str
        symbol: str
        shares: int

    llm = FakeLLM([make_response(content='{"action": "buy", "symbol": "AAPL", "shares": 10}')])
    agent = kg.Agent(role="t", goal="t", llm=llm, output_schema=Decision)

    result = await agent.run({"input": "decide"})
    out = result["output"]
    assert isinstance(out, Decision)
    assert out.action == "buy" and out.symbol == "AAPL" and out.shares == 10


async def test_agent_output_schema_strips_markdown_fences():
    @dataclass
    class Decision:
        action: str

    llm = FakeLLM([make_response(content='```json\n{"action": "hold"}\n```')])
    agent = kg.Agent(role="t", goal="t", llm=llm, output_schema=Decision)

    result = await agent.run({"input": "decide"})
    assert result["output"].action == "hold"


async def test_agent_output_schema_parse_failure_raises():
    @dataclass
    class Decision:
        action: str

    llm = FakeLLM([make_response(content="not json at all")])
    agent = kg.Agent(role="t", goal="t", llm=llm, output_schema=Decision)

    with pytest.raises(ValueError, match="Failed to parse structured output"):
        await agent.run({"input": "decide"})


async def test_agent_output_schema_pydantic():
    pydantic = pytest.importorskip("pydantic")

    class Decision(pydantic.BaseModel):
        action: str
        symbol: str
        shares: int

    llm = FakeLLM([make_response(content='{"action": "sell", "symbol": "NVDA", "shares": 5}')])
    agent = kg.Agent(role="t", goal="t", llm=llm, output_schema=Decision)

    result = await agent.run({"input": "decide"})
    out = result["output"]
    assert isinstance(out, Decision)
    assert out.action == "sell" and out.shares == 5
