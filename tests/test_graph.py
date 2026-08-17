"""Smoke tests for kitegen core — graph, checkpoint, interrupt, retry."""

import asyncio

import kitegen as kg


async def test_basic_graph():
    async def a(state):
        state["a"] = True
        return state

    async def b(state):
        state["b"] = True
        return state

    g = kg.Graph()
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_edge("a", "b")
    g.set_entry_point("a")
    agent = g.compile()

    result = await agent.invoke({}, thread_id="t1")
    assert result["a"] and result["b"]


async def test_checkpoint_restore():
    """Checkpoint state merges with new state: new keys win, saved keys persist."""
    async def a(state):
        state["a"] = True
        return state

    g = kg.Graph()
    g.add_node("a", a)
    g.set_entry_point("a")

    saver = kg.MemorySaver()
    agent1 = g.compile(checkpointer=saver)
    await agent1.invoke({"x": 1, "keep": "me"}, thread_id="t2")

    agent2 = g.compile(checkpointer=saver)
    result = await agent2.invoke({"x": 2}, thread_id="t2")
    assert result["a"] is True
    assert result["x"] == 2  # new state wins over checkpoint
    assert result["keep"] == "me"  # saved keys persist


async def test_conditional_routing():
    async def router(state):
        return "b" if state.get("go_b") else "done"

    async def start(state):
        state["started"] = True
        return state

    async def b(state):
        state["to"] = "b"
        return state

    async def done(state):
        state["to"] = "done"
        return state

    g = kg.Graph()
    g.add_node("start", start)
    g.add_node("b", b)
    g.add_node("done", done)
    g.add_conditional_edges("start", router, {"b": "b", "done": "done"})
    g.set_entry_point("start")

    agent = g.compile()
    r1 = await agent.invoke({"go_b": False}, thread_id="t3a")
    assert r1["to"] == "done"

    r2 = await agent.invoke({"go_b": True}, thread_id="t3b")
    assert r2["to"] == "b"


async def test_interrupt_and_resume():
    async def review(state):
        resume = state.get("_resume_data")
        if resume is not None:
            state["done"] = True
            state["decision"] = resume
            return state
        await kg.interrupt({"action": "review", "cols": 5})

    g = kg.Graph()
    g.add_node("r", review)
    g.set_entry_point("r")
    agent = g.compile(checkpointer=kg.MemorySaver())

    interrupted = await agent.invoke({}, thread_id="t4")
    assert interrupted.get("_interrupted_at") == "r"

    resumed = await agent.resume({"approved": 3, "rejected": 2}, thread_id="t4")
    assert resumed["done"]
    assert resumed["decision"] == {"approved": 3, "rejected": 2}


async def test_llm_retry():
    class FlakyError(kg.LLMRetryableError):
        pass

    async def flaky(state):
        state["tries"] = state.get("tries", 0) + 1
        if state["tries"] < 3:
            raise FlakyError("fail")
        state["ok"] = True
        return state

    g = kg.Graph()
    g.add_node("f", flaky)
    g.set_entry_point("f")

    agent = g.compile()
    result = await agent.invoke({}, thread_id="t5", max_retries=3)
    assert result["ok"] and result["tries"] == 3


async def test_circuit_breaker():
    import contextlib

    cb = kg.CircuitBreaker("test", failure_threshold=2, recovery_timeout=60)

    async def fail():
        raise RuntimeError("boom")

    for _ in range(2):
        with contextlib.suppress(RuntimeError):
            await cb.call(fail)

    try:
        await cb.call(fail)
    except kg.CircuitOpenError:
        assert True


async def test_sync_node_auto_wrap():
    def sync_node(state):
        state["done"] = True
        return state

    g = kg.Graph()
    g.add_node("s", sync_node)
    g.set_entry_point("s")

    agent = g.compile()
    result = await agent.invoke({}, thread_id="t6")
    assert result["done"]


# ── Streaming tests ───────────────────────────────────────────────────────


async def test_invoke_stream_basic():
    """invoke_stream yields NodeStart, NodeEnd, Complete in order."""
    async def a(state):
        state["a"] = 1
        return state

    async def b(state):
        state["b"] = 2
        return state

    g = kg.Graph()
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_edge("a", "b")
    g.set_entry_point("a")
    agent = g.compile()

    events = []
    async for event in agent.invoke_stream({}, thread_id="ts1"):
        events.append(event)

    assert len(events) == 5  # NodeStart a, NodeEnd a, NodeStart b, NodeEnd b, Complete
    assert isinstance(events[0], kg.NodeStart) and events[0].node == "a"
    assert isinstance(events[1], kg.NodeEnd) and events[1].node == "a"
    assert isinstance(events[2], kg.NodeStart) and events[2].node == "b"
    assert isinstance(events[3], kg.NodeEnd) and events[3].node == "b"
    assert isinstance(events[4], kg.Complete)


async def test_invoke_stream_custom_events():
    """stream_event() inside a node yields Custom events."""
    async def generate(state):
        await kg.stream_event({"type": "token", "content": "Hello"})
        await kg.stream_event({"type": "token", "content": " World"})
        state["result"] = "Hello World"
        return state

    g = kg.Graph()
    g.add_node("generate", generate)
    g.set_entry_point("generate")
    agent = g.compile()

    custom_events = []
    async for event in agent.invoke_stream({}, thread_id="ts2"):
        if isinstance(event, kg.Custom):
            custom_events.append(event)

    assert len(custom_events) == 2
    assert custom_events[0].data == {"type": "token", "content": "Hello"}
    assert custom_events[1].data == {"type": "token", "content": " World"}


async def test_invoke_stream_interrupt_and_resume():
    """Interrupt yields an Interrupt event; resume_stream continues."""
    async def review(state):
        resume = state.get("_resume_data")
        if resume is not None:
            state["approved"] = resume["ok"]
            return state
        await kg.interrupt({"action": "approve", "item": "listing"})

    async def publish(state):
        state["published"] = True
        return state

    g = kg.Graph()
    g.add_node("review", review)
    g.add_node("publish", publish)
    g.add_edge("review", "publish")
    g.set_entry_point("review")
    agent = g.compile(checkpointer=kg.MemorySaver())

    # First run: should interrupt
    interrupt_event = None
    async for event in agent.invoke_stream({}, thread_id="ts3"):
        if isinstance(event, kg.Interrupt):
            interrupt_event = event
            break

    assert interrupt_event is not None
    assert interrupt_event.node == "review"
    assert interrupt_event.payload == {"action": "approve", "item": "listing"}

    # Resume
    events = []
    async for event in agent.resume_stream({"ok": True}, thread_id="ts3"):
        events.append(type(event).__name__)

    assert "NodeStart" in events  # review re-runs
    assert "NodeEnd" in events    # review completes
    assert "Complete" in events   # graph finishes


async def test_invoke_stream_node_error():
    """NodeError is yielded when a node raises an unhandled exception."""
    async def bad_node(state):
        raise ValueError("something went wrong")
        return state  # unreachable

    g = kg.Graph()
    g.add_node("bad", bad_node)
    g.set_entry_point("bad")
    agent = g.compile()

    error_event = None
    try:
        async for event in agent.invoke_stream({}, thread_id="ts4"):
            if isinstance(event, kg.NodeError):
                error_event = event
    except ValueError:
        pass

    assert error_event is not None
    assert error_event.node == "bad"
    assert "something went wrong" in error_event.error


async def test_stream_event_noop_outside_invoke_stream():
    """stream_event() is a no-op when called outside invoke_stream()."""
    await kg.stream_event({"type": "token", "content": "ignored"})
    # Should not raise — just a no-op


async def test_invoke_stream_conditional():
    """invoke_stream respects conditional edges."""
    async def router(state):
        return "skip" if state.get("skip") else "process"

    async def start(state):
        return state

    async def process(state):
        state["processed"] = True
        return state

    g = kg.Graph()
    g.add_node("start", start)
    g.add_node("process", process)
    g.add_conditional_edges("start", router, {"skip": None, "process": "process"})
    g.set_entry_point("start")

    agent = g.compile()

    # Route to process
    events = []
    async for event in agent.invoke_stream({"skip": False}, thread_id="ts5a"):
        events.append(type(event).__name__)
    assert "Complete" in events

    # Route to end (skip)
    events = []
    async for event in agent.invoke_stream({"skip": True}, thread_id="ts5b"):
        events.append(type(event).__name__)
    assert "Complete" in events


async def test_invoke_stream_cancel_mid_node():
    """Closing the stream mid-node cancels the in-flight node task."""
    node_cancelled = asyncio.Event()

    async def slow_node(state):
        await kg.stream_event({"type": "ping"})  # node is now running
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            node_cancelled.set()
            raise
        return state

    g = kg.Graph()
    g.add_node("slow", slow_node)
    g.set_entry_point("slow")
    agent = g.compile()

    async def consume():
        async for event in agent.invoke_stream({}, thread_id="ts6"):
            if isinstance(event, kg.Custom):
                break  # close the stream while the node is mid-execution

    await asyncio.wait_for(consume(), timeout=5)
    # The slow node must have been cancelled, not left running
    await asyncio.wait_for(node_cancelled.wait(), timeout=2)
    assert node_cancelled.is_set()
