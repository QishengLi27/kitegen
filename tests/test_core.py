"""Tests for kitegen.core — Executable, Context, events, retry, interrupt."""

import pytest

import kitegen as kg


async def test_retry_policy_exponential():
    policy = kg.RetryPolicy(max_retries=3, base_delay=1.0, exponential=True)
    assert policy.delay_for(0) == 1.0
    assert policy.delay_for(1) == 2.0
    assert policy.delay_for(2) == 4.0
    assert policy.delay_for(10) == 60.0  # capped by max_delay


async def test_context_copy():
    ctx = kg.Context(thread_id="t1", node_name="n1")
    ctx2 = ctx.copy(thread_id="t2")
    assert ctx2.thread_id == "t2"
    assert ctx2.node_name == "n1"
    # Original unchanged
    assert ctx.thread_id == "t1"


async def test_function_executable_sync():
    def add(state, context):
        state["x"] = state.get("x", 0) + 1
        return state

    exe = kg.FunctionExecutable(add)
    result = await exe.execute({"x": 1}, kg.Context())
    assert result["x"] == 2


async def test_function_executable_async():
    async def add(state, context):
        state["x"] = state.get("x", 0) + 1
        return state

    exe = kg.as_executable(add)
    result = await exe.execute({"x": 1}, kg.Context())
    assert result["x"] == 2


async def test_runnable_run():
    class Doubler(kg.Runnable):
        async def execute(self, state, context):
            state["x"] *= 2
            return state

    d = Doubler()
    result = await d.run({"x": 3})
    assert result["x"] == 6
    assert "_thread_id" in result


async def test_runnable_stream():
    class Emitter(kg.Runnable):
        async def execute(self, state, context):
            context.stream(kg.NodeStart(node="emitter"))
            return state

    events = []
    async for event in Emitter().stream({}):
        events.append(event)

    types = [type(e).__name__ for e in events]
    assert "NodeStart" in types
    assert "Complete" in types
    # Last event should be the returned state dict
    assert isinstance(events[-1], dict)


async def test_interrupt_signal():
    """interrupt() raises InterruptError, which is a subclass of KitegenError."""
    with pytest.raises(kg.InterruptError) as exc_info:
        await kg.interrupt({"action": "review"})
    assert exc_info.value.payload == {"action": "review"}
    assert isinstance(exc_info.value, kg.KitegenError)


async def test_execute_with_retry_success_after_failure():
    class Flaky(kg.LLMRetryableError):
        pass

    calls = []

    async def work():
        calls.append(len(calls))
        if len(calls) < 3:
            raise Flaky("fail")
        return "ok"

    ctx = kg.Context(retry_policy=kg.RetryPolicy(max_retries=3, base_delay=0.0))
    result = await kg.execute_with_retry(work, context=ctx)
    assert result == "ok"
    assert len(calls) == 3


async def test_execute_with_retry_exhausted():
    class Flaky(kg.LLMRetryableError):
        pass

    async def work():
        raise Flaky("always fail")

    ctx = kg.Context(retry_policy=kg.RetryPolicy(max_retries=2, base_delay=0.0))
    with pytest.raises(Flaky):
        await kg.execute_with_retry(work, context=ctx)


async def test_node_trace_dataclass():
    trace = kg.NodeTrace(node="a", started_at=1.0)
    assert trace.finished_at == 0.0
    assert trace.error is None


async def test_event_dataclasses():
    start = kg.NodeStart(node="a")
    end = kg.NodeEnd(node="a", trace=kg.NodeTrace(node="a", started_at=0.0))
    error = kg.NodeError(node="a", error="boom")
    complete = kg.Complete()
    custom = kg.Custom(node="a", data={"x": 1})

    assert start.node == "a"
    assert end.node == "a"
    assert end.trace.node == "a"
    assert error.error == "boom"
    assert custom.data == {"x": 1}
    assert isinstance(complete, kg.Complete)
