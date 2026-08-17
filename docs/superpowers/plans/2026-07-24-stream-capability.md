# Stream Capability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add streaming at two levels: `chat_stream()` for token-by-token LLM output, and `invoke_stream()` for graph execution events.

**Architecture:** Event dataclasses flow through a `ContextVar`-backed `asyncio.Queue` — nodes call `stream_event()` to push `Custom` events into the queue, while `invoke_stream()` runs the node in a background task and yields queue items as they arrive alongside lifecycle events (`NodeStart`, `NodeEnd`, `Interrupt`, `Complete`, `NodeError`). `chat_stream()` is a simple async generator wrapping OpenAI's `stream=True`.

**Tech Stack:** Python 3.10+, `openai>=1.0`, `asyncio`

## Global Constraints

- No breaking changes to existing `chat()`, `invoke()`, `resume()`
- All new code matches existing patterns: plain dicts, dataclasses, no TypedDict/Annotated reducers
- No new dependencies beyond `openai` (already required)

---

### Task 1: Event dataclasses in `graph.py`

**Files:**
- Modify: `src/kitegen/graph.py`

**Interfaces:**
- Produces: `NodeStart`, `NodeEnd`, `NodeError`, `Interrupt`, `Complete`, `Custom` dataclasses — exported from `kitegen.graph`, consumed by Tasks 3-7

Add seven event dataclasses near the top of `graph.py`, right after the `NodeTrace` dataclass (line ~44).

- [ ] **Step 1: Add event dataclasses to `graph.py`**

In `src/kitegen/graph.py`, after the `NodeTrace` dataclass (after line 43), insert:

```python
# ── Stream Events ──────────────────────────────────────────────────────────

@dataclass
class NodeStart:
    """Emitted when a node begins execution."""
    node: str


@dataclass
class NodeEnd:
    """Emitted when a node completes successfully."""
    node: str
    trace: NodeTrace


@dataclass
class NodeError:
    """Emitted when a node fails (retries exhausted)."""
    node: str
    error: str


@dataclass
class Interrupt:
    """Emitted when a node calls interrupt()."""
    node: str
    payload: Any


@dataclass
class Complete:
    """Emitted when the graph finishes all nodes."""
    pass


@dataclass
class Custom:
    """Emitted when a node calls stream_event(data)."""
    node: str
    data: Any
```

- [ ] **Step 2: Verify the file parses**

Run: `python -c "from kitegen.graph import NodeStart, NodeEnd, NodeError, Interrupt, Complete, Custom; print('OK')"`
Expected: prints `OK`

- [ ] **Step 3: Commit**

```bash
git add src/kitegen/graph.py
git commit -m "feat: add stream event dataclasses"
```

---

### Task 2: `chat_stream()` in `llm.py`

**Files:**
- Modify: `src/kitegen/llm.py`

**Interfaces:**
- Produces: `chat_stream(system_prompt, user_message, model, temperature, max_tokens, tracker) -> AsyncIterator[str]` — consumed by user code, independent of agent streaming

Add `chat_stream()` to `llm.py` after the `chat()` function (after line ~87). Uses `stream=True` on the OpenAI API call.

- [ ] **Step 1: Write `chat_stream()`**

In `src/kitegen/llm.py`, after the `chat()` function (after line 87), insert:

```python
async def chat_stream(
    system_prompt: str,
    user_message: str,
    model: str = "deepseek-chat",
    temperature: float = 0.0,
    max_tokens: int = 2000,
    tracker: TokenTracker | None = None,
) -> "AsyncIterator[str]":
    """Stream chat response tokens as they arrive.

    Args:
        system_prompt: System-level instruction.
        user_message: The user's question or input.
        model: Model name (deepseek-chat, gpt-4o, etc.).
        temperature: 0.0 for deterministic, higher for creative.
        max_tokens: Max output tokens.
        tracker: Optional TokenTracker to accumulate usage from the final chunk.

    Yields:
        Text deltas (str) as they arrive from the API.

    Usage:
        async for chunk in chat_stream("You are helpful", "Hello"):
            print(chunk, end="")
    """
    from collections.abc import AsyncIterator

    client = await _get_client()

    response = await client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        stream=True,
        stream_options={"include_usage": True},
    )

    async for chunk in response:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content
        if chunk.usage:
            usage = Usage.from_openai(chunk.usage)
            if tracker:
                tracker.record(model, usage)
```

Add `AsyncIterator` to the imports at the top of `llm.py`. Change the import line:

```python
from collections.abc import AsyncIterator
```

If this import already exists, verify the line is present; if not, add it.

- [ ] **Step 2: Verify the import**

Check the imports section of `src/kitegen/llm.py` — ensure `from collections.abc import AsyncIterator` exists. If not, add it. The file currently imports `from typing import Any`. Add the `AsyncIterator` import:

Replace:
```python
from typing import Any
```

With:
```python
from collections.abc import AsyncIterator
from typing import Any
```

- [ ] **Step 3: Verify the file parses**

Run: `python -c "from kitegen.llm import chat_stream; print('OK')"`
Expected: prints `OK`

- [ ] **Step 4: Commit**

```bash
git add src/kitegen/llm.py
git commit -m "feat: add chat_stream() for token-by-token LLM output"
```

---

### Task 3: `stream_event()` context function + context vars

**Files:**
- Modify: `src/kitegen/graph.py`

**Interfaces:**
- Consumes: `Custom` dataclass (Task 1), `_current_node_name` ContextVar (new)
- Produces: `stream_event(data) -> None` — pushes a `Custom` event onto the stream queue; no-op if not inside `invoke_stream()`

Add the `stream_event()` function and two ContextVars (`_stream_queue`, `_current_node_name`) to `graph.py`.

- [ ] **Step 1: Add ContextVars after existing `_current_agent`**

In `src/kitegen/graph.py`, find the line:
```python
_current_agent: contextvars.ContextVar[Agent] = contextvars.ContextVar("kitegen_agent")
```
(around line 67)

After it, insert:

```python
_stream_queue: contextvars.ContextVar = contextvars.ContextVar(
    "kitegen_stream_queue", default=None
)
_current_node_name: contextvars.ContextVar[str] = contextvars.ContextVar(
    "kitegen_current_node", default=""
)
```

- [ ] **Step 2: Add `stream_event()` after `interrupt()`**

In `src/kitegen/graph.py`, find the `interrupt()` function (around line 64). After its closing (after line 65), insert:

```python
async def stream_event(data: Any) -> None:
    """Emit custom data into the enclosing agent's event stream.

    Call this inside a node function during ``invoke_stream()``.
    The data is yielded as a ``Custom`` event to the caller.

    If called outside of ``invoke_stream()`` (e.g., from ``invoke()``),
    this is a no-op.

    Usage:
        async for chunk in chat_stream(...):
            await stream_event({"type": "token", "content": chunk})
    """
    import asyncio as _asyncio

    q = _stream_queue.get()
    if q is not None:
        await q.put(Custom(node=_current_node_name.get(), data=data))
```

Note: `asyncio` is already imported at the top of `graph.py` (line 17), so the local `import asyncio as _asyncio` is just for the type hint on `Queue`. We access it via the variable `q` which is already typed.

- [ ] **Step 3: Verify the file parses**

Run: `python -c "from kitegen.graph import stream_event; print('OK')"`
Expected: prints `OK`

- [ ] **Step 4: Commit**

```bash
git add src/kitegen/graph.py
git commit -m "feat: add stream_event() context function"
```

---

### Task 4: `_execute_node()` helper method on `Agent`

**Files:**
- Modify: `src/kitegen/graph.py`

**Interfaces:**
- Consumes: `_current_agent` ContextVar, `_current_node_name` ContextVar (Task 3), `NodeTrace`, `InterruptError`, `LLMRetryableError` (existing)
- Produces: `Agent._execute_node(node_fn, state, node_name, max_retries, trace) -> tuple[dict, Any]` — returns `(state, interrupt_payload)` where `interrupt_payload` is `None` on success or the payload from `InterruptError`

Extract the node execution + retry logic from `invoke()` into a reusable helper. This is shared by both `invoke()` and `invoke_stream()`.

- [ ] **Step 1: Add `_execute_node()` to the `Agent` class**

In `src/kitegen/graph.py`, inside the `Agent` class, add this method. Put it after `__init__()` (after line 130):

```python
    async def _execute_node(
        self,
        node_fn: NodeFn,
        state: dict[str, Any],
        node_name: str,
        max_retries: int,
        trace: NodeTrace,
    ) -> tuple[dict[str, Any], Any]:
        """Execute a node with retry logic.

        Returns:
            (state, interrupt_payload) — interrupt_payload is None on success,
            or the value passed to interrupt() if the node paused.
        """
        for attempt in range(max_retries + 1):
            token = _current_agent.set(self)
            _current_node_name.set(node_name)
            try:
                state = await node_fn(state)
                trace.finished_at = time.time()
                return state, None
            except InterruptError as e:
                trace.finished_at = time.time()
                return state, e.payload
            except LLMRetryableError as e:
                if attempt == max_retries:
                    trace.error = str(e)
                    raise
                logger.warning(
                    "[kitegen] Node '%s' retry %d/%d: %s",
                    node_name, attempt + 1, max_retries, e,
                )
                await asyncio.sleep(2 ** attempt)
            except Exception:
                raise
            finally:
                _current_agent.reset(token)
```

- [ ] **Step 2: Refactor `invoke()` to use `_execute_node()`**

In `src/kitegen/graph.py`, find the `invoke()` method. Replace the inner for-loop (lines 160-181) that does:

```python
            for attempt in range(max_retries + 1):
                token = _current_agent.set(self)
                try:
                    state = await node_fn(state)
                    break
                except InterruptError:
                    state["_interrupted_at"] = current
                    state["_node_history"].append(trace)
                    if self._checkpointer:
                        await self._checkpointer.save(state, tid)
                    return state
                except LLMRetryableError as e:
                    if attempt == max_retries:
                        trace.error = str(e)
                        raise
                    logger.warning("[kitegen] Node '%s' retry %d/%d: %s", current, attempt + 1, max_retries, e)
                    await asyncio.sleep(2 ** attempt)
                except Exception as e:
                    trace.error = str(e)
                    raise
                finally:
                    _current_agent.reset(token)

            trace.finished_at = time.time()
            state["_node_history"].append(trace)
```

Replace with:

```python
            try:
                state, interrupt_payload = await self._execute_node(
                    node_fn, state, current, max_retries, trace,
                )
            except Exception as e:
                trace.error = str(e)
                raise

            state["_node_history"].append(trace)

            if interrupt_payload is not None:
                state["_interrupted_at"] = current
                if self._checkpointer:
                    await self._checkpointer.save(state, tid)
                return state
```

- [ ] **Step 3: Run existing tests to verify no regression**

Run: `python -m pytest tests/test_graph.py -v`
Expected: all 6 tests pass

- [ ] **Step 4: Commit**

```bash
git add src/kitegen/graph.py
git commit -m "refactor: extract _execute_node() helper, shared by invoke() and invoke_stream()"
```

---

### Task 5: `invoke_stream()` and `resume_stream()` on `Agent`

**Files:**
- Modify: `src/kitegen/graph.py`

**Interfaces:**
- Consumes: Event dataclasses (Task 1), `stream_event()` + ContextVars (Task 3), `_execute_node()` (Task 4)
- Produces:
  - `Agent.invoke_stream(state, thread_id, max_retries) -> AsyncIterator[NodeStart | NodeEnd | NodeError | Interrupt | Complete | Custom]`
  - `Agent.resume_stream(data, thread_id) -> AsyncIterator[NodeStart | NodeEnd | NodeError | Interrupt | Complete | Custom]`

Add `invoke_stream()` and `resume_stream()` to the `Agent` class. The node runs in a background `asyncio.Task` so that `stream_event()` calls from within the node are yielded to the caller in real-time.

- [ ] **Step 1: Add `invoke_stream()` to `Agent`**

In `src/kitegen/graph.py`, inside the `Agent` class, add this method. Put it after `invoke()`:

```python
    async def invoke_stream(
        self,
        state: dict[str, Any],
        thread_id: str | None = None,
        max_retries: int = 2,
    ):
        """Execute the graph and yield events as they happen.

        Like ``invoke()``, but yields typed event objects instead of
        returning the final state. Inside nodes, call ``stream_event(data)``
        to push ``Custom`` events into this stream.

        Usage:
            async for event in agent.invoke_stream(state, thread_id="t1"):
                match event:
                    case NodeStart(node=name): ...
                    case NodeEnd(node=name): ...
                    case Custom(data=d): ...
                    case Interrupt(payload=p): ...
        """
        tid = thread_id or uuid.uuid4().hex[:12]

        if self._checkpointer and not state.get("_resume_from"):
            saved = await self._checkpointer.load(tid)
            if saved:
                state = saved
                logger.info("[kitegen] Restored state: %s", tid)

        state["_thread_id"] = tid
        state.setdefault("_node_history", [])

        current: str | None = state.get("_resume_from") or self._entry
        state.pop("_resume_from", None)

        q: asyncio.Queue = asyncio.Queue()
        token_q = _stream_queue.set(q)

        try:
            while current is not None:
                node_fn = self._nodes.get(current)
                if node_fn is None:
                    raise ValueError(f"Node '{current}' not found")

                yield NodeStart(node=current)

                trace = NodeTrace(node=current, started_at=time.time())

                # Run node in background task so we can yield stream events
                node_task = asyncio.ensure_future(
                    self._execute_node(node_fn, state, current, max_retries, trace)
                )

                # Stream loop: yield events while node runs
                while True:
                    if node_task.done():
                        break
                    getter = asyncio.ensure_future(q.get())
                    done, pending = await asyncio.wait(
                        [node_task, getter],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if getter in done:
                        yield getter.result()
                    if getter in pending:
                        getter.cancel()

                # Process node result
                try:
                    state, interrupt_payload = node_task.result()
                except Exception as e:
                    yield NodeError(node=current, error=str(e))
                    # Drain remaining events before re-raising
                    while not q.empty():
                        yield q.get_nowait()
                    raise

                state["_node_history"].append(trace)

                if interrupt_payload is not None:
                    state["_interrupted_at"] = current
                    if self._checkpointer:
                        await self._checkpointer.save(state, tid)
                    yield Interrupt(node=current, payload=interrupt_payload)
                    # Drain remaining events
                    while not q.empty():
                        yield q.get_nowait()
                    return

                yield NodeEnd(node=current, trace=trace)

                # Drain any remaining stream events
                while not q.empty():
                    yield q.get_nowait()

                current = await self._next(current, state)

            state["_completed"] = True
            if self._checkpointer:
                await self._checkpointer.save(state, tid)
            yield Complete()
        finally:
            _stream_queue.reset(token_q)
```

- [ ] **Step 2: Add `resume_stream()` to `Agent`**

In `src/kitegen/graph.py`, inside the `Agent` class, add this method after `resume()`:

```python
    async def resume_stream(self, data: Any, thread_id: str):
        """Resume a paused graph, yielding streaming events.

        Streaming variant of ``resume()``. Reloads the checkpoint,
        passes resume data to the node, and yields events via
        ``invoke_stream()``.

        Usage:
            async for event in agent.resume_stream(decision, thread_id="t1"):
                ...
        """
        if not self._checkpointer:
            raise ValueError("resume_stream() requires a checkpointer")

        state = await self._checkpointer.load(thread_id)

        if not state:
            raise ValueError(f"No state found for thread '{thread_id}'")
        if not state.get("_interrupted_at"):
            raise ValueError("Graph was not interrupted. Nothing to resume.")

        state["_resume_from"] = state.pop("_interrupted_at")
        state["_resume_data"] = data

        async for event in self.invoke_stream(state, thread_id=thread_id):
            yield event
```

- [ ] **Step 3: Verify the file parses**

Run: `python -c "import kitegen as kg; g = kg.Graph(); g.add_node('a', lambda s: s); g.set_entry_point('a'); a = g.compile(); print(hasattr(a, 'invoke_stream'), hasattr(a, 'resume_stream'))"`
Expected: `True True`

- [ ] **Step 4: Run existing tests to verify no regression**

Run: `python -m pytest tests/test_graph.py -v`
Expected: all 6 tests pass

- [ ] **Step 5: Commit**

```bash
git add src/kitegen/graph.py
git commit -m "feat: add invoke_stream() and resume_stream() for streaming graph execution"
```

---

### Task 6: Export new symbols from `__init__.py`

**Files:**
- Modify: `src/kitegen/__init__.py`

**Interfaces:**
- Consumes: All new symbols from Tasks 1-5
- Produces: `__all__` updated with `chat_stream`, `stream_event`, `NodeStart`, `NodeEnd`, `NodeError`, `Interrupt`, `Complete`, `Custom`

- [ ] **Step 1: Update imports and `__all__` in `__init__.py`**

In `src/kitegen/__init__.py`, update the imports and exports.

Replace the entire file content with:

```python
"""kitegen — A lightweight agent framework.

State + Node + Edge + Checkpoint + interrupt() + stream_event(). Nothing else.

Usage:
    import kitegen as kg

    graph = kg.Graph()
    graph.add_node("classify", classify_fn)
    graph.add_node("verify", verify_fn)
    graph.add_edge("classify", "verify")
    graph.set_entry_point("classify")

    agent = graph.compile(checkpointer=kg.MemorySaver())

    # Non-streaming
    result = await agent.invoke({"input": "classify this table"})

    # Streaming
    async for event in agent.invoke_stream({"input": "classify this table"}):
        match event:
            case kg.NodeStart(node=name): ...
            case kg.Custom(data=d): ...
            case kg.Complete(): ...
"""

from kitegen.checkpoint import Checkpointer, MemorySaver, PostgresSaver
from kitegen.graph import (
    Agent,
    Complete,
    Custom,
    Graph,
    Interrupt,
    LLMRetryableError,
    NodeEnd,
    NodeError,
    NodeStart,
    NodeTrace,
    interrupt,
    stream_event,
)
from kitegen.llm import ChatResponse, chat, chat_stream, chat_structured
from kitegen.resilience import CircuitBreaker, CircuitOpenError, TokenTracker, Usage

__version__ = "0.1.0"

__all__ = [
    "Graph",
    "Agent",
    "interrupt",
    "stream_event",
    "LLMRetryableError",
    "NodeTrace",
    "NodeStart",
    "NodeEnd",
    "NodeError",
    "Interrupt",
    "Complete",
    "Custom",
    "Checkpointer",
    "MemorySaver",
    "PostgresSaver",
    "CircuitBreaker",
    "TokenTracker",
    "Usage",
    "CircuitOpenError",
    "chat",
    "chat_stream",
    "chat_structured",
    "ChatResponse",
]
```

- [ ] **Step 2: Verify all symbols are importable**

Run: `python -c "import kitegen as kg; print(kg.chat_stream, kg.stream_event, kg.NodeStart, kg.NodeEnd, kg.NodeError, kg.Interrupt, kg.Complete, kg.Custom); print('OK')"`
Expected: prints references to each symbol followed by `OK`

- [ ] **Step 3: Commit**

```bash
git add src/kitegen/__init__.py
git commit -m "feat: export stream symbols from top-level package"
```

---

### Task 7: Tests

**Files:**
- Modify: `tests/test_graph.py`

**Interfaces:**
- Consumes: All exported symbols from `kitegen` (Tasks 1-6)

Add streaming-specific tests to the existing test suite. Each test function is an `async def test_*` function (pytest-asyncio is configured via `asyncio_mode = "auto"` in pyproject.toml).

- [ ] **Step 1: Add test for basic `invoke_stream()`**

Append to `tests/test_graph.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_graph.py::test_invoke_stream_basic -v`
Expected: PASS (should work if Tasks 1-6 are complete)

- [ ] **Step 3: Add test for `stream_event()` Custom events**

Append to `tests/test_graph.py`:

```python
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
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/test_graph.py::test_invoke_stream_custom_events -v`
Expected: PASS

- [ ] **Step 5: Add test for `invoke_stream()` with `interrupt()` and `resume_stream()`**

Append to `tests/test_graph.py`:

```python
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
```

- [ ] **Step 6: Run the test**

Run: `python -m pytest tests/test_graph.py::test_invoke_stream_interrupt_and_resume -v`
Expected: PASS

- [ ] **Step 7: Add test for `invoke_stream()` with node error**

Append to `tests/test_graph.py`:

```python
async def test_invoke_stream_node_error():
    """NodeError is yielded when a node raises an unhandled exception."""
    async def bad_node(state):
        raise ValueError("something went wrong")
        return state

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
```

- [ ] **Step 8: Run the test**

Run: `python -m pytest tests/test_graph.py::test_invoke_stream_node_error -v`
Expected: PASS

- [ ] **Step 9: Add test for `stream_event()` is a no-op outside `invoke_stream()`**

Append to `tests/test_graph.py`:

```python
async def test_stream_event_noop_outside_invoke_stream():
    """stream_event() is a no-op when called outside invoke_stream()."""
    await kg.stream_event({"type": "token", "content": "ignored"})
    # Should not raise — just a no-op
```

- [ ] **Step 10: Run the test**

Run: `python -m pytest tests/test_graph.py::test_stream_event_noop_outside_invoke_stream -v`
Expected: PASS

- [ ] **Step 11: Add test for `invoke_stream()` with conditional routing**

Append to `tests/test_graph.py`:

```python
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
```

There is a bug in this test — `add_conditional_edges` is called twice. Let's fix that:

```python
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
```

Wait, `{"skip": None, "process": "process"}` — when router returns "skip", the mapping says `None`, so the graph should end. Let me verify this works with the existing `_next()` method:

```python
async def _next(self, current: str, state: dict) -> str | None:
    if current in self._conditional:
        router, mapping = self._conditional[current]
        key = await router(state) if asyncio.iscoroutinefunction(router) else router(state)
        return mapping.get(key)
    return self._edges.get(current)
```

Yes, if mapping returns None for a key, `mapping.get(key)` returns None, and the while loop in invoke terminates. Good.

- [ ] **Step 12: Run the test**

Run: `python -m pytest tests/test_graph.py::test_invoke_stream_conditional -v`
Expected: PASS

- [ ] **Step 13: Run the full test suite**

Run: `python -m pytest tests/test_graph.py -v`
Expected: all 11 tests pass (6 original + 5 new)

- [ ] **Step 14: Commit**

```bash
git add tests/test_graph.py
git commit -m "test: add streaming tests for invoke_stream, stream_event, resume_stream"
```
