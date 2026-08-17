"""Tests for kitegen.agent — Agent class with tool-calling loop."""
import pytest
import kitegen as kg


# ── Helpers ────────────────────────────────────────────────────────────────

class FakeLLM(kg.LLMAdapter):
    """Fake LLM that returns predefined responses."""

    def __init__(self, responses: list[kg.LLMResponse]):
        self.responses = responses
        self.calls: list[tuple] = []

    async def chat(self, messages, tools=None, model=None, temperature=None,
                   max_tokens=None, **kwargs):
        self.calls.append((messages, tools))
        if not self.responses:
            return kg.LLMResponse(content="no more responses")
        return self.responses.pop(0)


def make_response(content=None, tool_calls=None, model="test"):
    return kg.LLMResponse(
        content=content,
        tool_calls=tool_calls or [],
        usage=kg.Usage(),
        model=model,
    )


# ── Tests ─────────────────────────────────────────────────────────────────


async def test_agent_without_tools_returns_content():
    """Agent without tools calls LLM once and stores output."""
    llm = FakeLLM([make_response(content="The answer is 42.")])
    agent = kg.Agent(
        role="answer bot",
        goal="answer questions",
        llm=llm,
    )

    result = await agent.run({"input": "what is the answer?"})

    assert result["output"] == "The answer is 42."
    assert len(llm.calls) == 1


async def test_agent_with_tools_calls_tool_once():
    """Agent with tools executes a tool call and continues."""
    llm = FakeLLM([
        make_response(tool_calls=[
            kg.ToolCall(id="c1", name="add", arguments={"a": 1, "b": 2}),
        ]),
        make_response(content="The sum is 3."),
    ])

    @kg.tool
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    agent = kg.Agent(
        role="calculator",
        goal="compute sums",
        tools=[add],
        llm=llm,
    )

    result = await agent.run({"input": "what is 1+2?"})

    assert result["output"] == "The sum is 3."
    assert len(llm.calls) == 2


async def test_agent_streams_tool_events():
    """Agent emits ToolCallEvent and ToolResultEvent via context.stream()."""
    llm = FakeLLM([
        make_response(tool_calls=[
            kg.ToolCall(id="c1", name="echo", arguments={"text": "hello"}),
        ]),
        make_response(content="done"),
    ])

    @kg.tool
    def echo(text: str) -> str:
        return text.upper()

    agent = kg.Agent(role="echo", goal="repeat", tools=[echo], llm=llm)

    events = []
    async for event in agent.stream({"input": "test"}):
        events.append(event)

    tool_calls = [e for e in events if isinstance(e, kg.ToolCallEvent)]
    tool_results = [e for e in events if isinstance(e, kg.ToolResultEvent)]

    assert len(tool_calls) == 1
    assert tool_calls[0].tool == "echo"
    assert tool_calls[0].arguments == {"text": "hello"}
    assert len(tool_results) == 1
    assert tool_results[0].tool == "echo"
    assert tool_results[0].result == "HELLO"


async def test_agent_max_iterations_stops_loop():
    """Agent stops after max_iterations even if LLM keeps calling tools."""
    llm = FakeLLM([
        make_response(tool_calls=[kg.ToolCall(id=f"c{i}", name="tick", arguments={})])
        for i in range(20)
    ])

    @kg.tool
    def tick() -> str:
        return "tock"

    agent = kg.Agent(
        role="clock", goal="tick forever", tools=[tick], llm=llm,
        max_iterations=3,
    )

    result = await agent.run({"input": "go"})

    # Should stop after max_iterations, returning whatever the LLM last said
    assert len(llm.calls) == 3
    assert result.get("output") is not None


async def test_agent_custom_system_prompt():
    """Agent uses custom system_prompt when provided."""
    llm = FakeLLM([make_response(content="ok")])
    agent = kg.Agent(
        role="ignored",
        goal="ignored",
        system_prompt="You are a pirate. Talk like one.",
        llm=llm,
    )

    await agent.run({"input": "hello"})

    sys_msg = llm.calls[0][0][0]
    assert sys_msg.role == "system"
    assert sys_msg.content == "You are a pirate. Talk like one."


async def test_agent_is_executable():
    """Agent implements the Executable protocol."""
    llm = FakeLLM([make_response(content="ok")])
    agent = kg.Agent(role="test", goal="test", llm=llm)
    assert isinstance(agent, kg.Executable)
    assert isinstance(agent, kg.Runnable)


async def test_agent_as_graph_node():
    """Agent can be added to a Graph and invoked."""
    llm = FakeLLM([make_response(content="classified")])
    agent = kg.Agent(role="classifier", goal="classify things", llm=llm)

    g = kg.Graph()
    g.add_node("classify", agent.execute)
    g.set_entry_point("classify")
    compiled = g.compile()

    result = await compiled.invoke({"input": "some text"}, thread_id="g1")
    assert result["output"] == "classified"


async def test_agent_handles_tool_error():
    """Agent feeds tool errors back to the LLM instead of crashing."""
    llm = FakeLLM([
        make_response(tool_calls=[
            kg.ToolCall(id="c1", name="risky", arguments={}),
        ]),
        make_response(content="handled the error"),
    ])

    @kg.tool
    def risky() -> str:
        raise RuntimeError("boom")

    agent = kg.Agent(role="safe", goal="handle errors", tools=[risky], llm=llm)

    result = await agent.run({"input": "go"})
    assert result["output"] == "handled the error"


async def test_agent_unknown_tool_reports_error():
    """Agent reports error when LLM calls a tool not in its tool list."""
    llm = FakeLLM([
        make_response(tool_calls=[
            kg.ToolCall(id="c1", name="nonexistent", arguments={}),
        ]),
        make_response(content="recovered from unknown tool"),
    ])

    agent = kg.Agent(role="test", goal="test", tools=[], llm=llm)

    result = await agent.run({"input": "go"})
    assert result["output"] == "recovered from unknown tool"


async def test_agent_renders_system_prompt():
    """Agent auto-renders system prompt from role+goal+personality."""
    llm = FakeLLM([make_response(content="ok")])
    agent = kg.Agent(
        role="chef",
        goal="cook delicious meals",
        personality="enthusiastic and precise",
        llm=llm,
    )

    await agent.run({"input": "recipe"})

    sys_msg = llm.calls[0][0][0]
    assert "chef" in sys_msg.content
    assert "cook delicious meals" in sys_msg.content
    assert "enthusiastic and precise" in sys_msg.content
