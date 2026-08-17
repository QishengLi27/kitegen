"""kitegen.graph — State machine for LLM agents.

Core API:
    graph = Graph()
    graph.add_node("classify", classify_fn)
    graph.add_edge("classify", "verify")
    graph.set_entry_point("classify")
    compiled = graph.compile(checkpointer=MemorySaver())
    result = await compiled.invoke({"input": "..."})

State is a plain dict. Nodes are async functions (state) -> state.
No TypedDict. No Annotated reducers. No Runnable abstractions.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from kitegen.core import (
    Complete,
    Custom,
    Interrupt,
    InterruptError,
    LLMRetryableError,
    NodeEnd,
    NodeError,
    NodeStart,
    NodeTrace,
    interrupt as _core_interrupt,
)

if TYPE_CHECKING:
    from kitegen.checkpoint import Checkpointer

logger = logging.getLogger("kitegen")

# ── Types ────────────────────────────────────────────────────────────────

NodeFn = Callable[[dict], Awaitable[dict]]
RouterFn = Callable[[dict], str]


async def interrupt(payload: Any = None) -> Any:
    """Pause the graph and return ``payload`` to the caller.

    Call this inside a node function. The graph stops immediately.
    Resume with agent.resume(data, thread_id).

    Usage:
        steward_input = await interrupt({"action": "review", "columns": [...]})
        # steward_input = {"decision": "approved"}
    """
    return await _core_interrupt(payload)


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
    q = _stream_queue.get()
    if q is not None:
        await q.put(Custom(node=_current_node_name.get(), data=data))


_current_agent: contextvars.ContextVar[Agent] = contextvars.ContextVar("kitegen_agent")
_stream_queue: contextvars.ContextVar = contextvars.ContextVar(
    "kitegen_stream_queue", default=None
)
_current_node_name: contextvars.ContextVar[str] = contextvars.ContextVar(
    "kitegen_current_node", default=""
)


# ── Graph Builder ────────────────────────────────────────────────────────

class Graph:
    """Declarative builder. Define nodes and edges, then compile()."""

    def __init__(self):
        self._nodes: dict[str, NodeFn] = {}
        self._edges: dict[str, str] = {}
        self._conditional_edges: dict[str, tuple[RouterFn, dict[str, str]]] = {}
        self._entry: str | None = None

    def add_node(self, name: str, fn: NodeFn) -> None:
        """Register a node function. Sync functions are auto-wrapped to async."""
        if not asyncio.iscoroutinefunction(fn):
            async def _wrapper(state: dict) -> dict:
                return fn(state)
            self._nodes[name] = _wrapper
        else:
            self._nodes[name] = fn

    def add_edge(self, from_node: str, to_node: str) -> None:
        self._edges[from_node] = to_node

    def add_conditional_edges(
        self, from_node: str, router: RouterFn, mapping: dict[str, str]
    ) -> None:
        self._conditional_edges[from_node] = (router, mapping)

    def set_entry_point(self, name: str) -> None:
        self._entry = name

    def compile(self, checkpointer: Checkpointer | None = None) -> Agent:
        if self._entry is None:
            raise ValueError("No entry point set. Call set_entry_point().")
        return Agent(
            nodes=self._nodes,
            edges=self._edges,
            conditional_edges=self._conditional_edges,
            entry=self._entry,
            checkpointer=checkpointer,
        )


# ── Agent (Compiled Graph) ──────────────────────────────────────────────

class Agent:
    """Runnable agent. Execute the graph with invoke(), supports interrupt/resume."""

    def __init__(
        self,
        nodes: dict[str, NodeFn],
        edges: dict[str, str],
        conditional_edges: dict[str, tuple[RouterFn, dict[str, str]]],
        entry: str,
        checkpointer: Checkpointer | None = None,
    ):
        self._nodes = nodes
        self._edges = edges
        self._conditional = conditional_edges
        self._entry = entry
        self._checkpointer = checkpointer

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

    async def invoke(
        self,
        state: dict[str, Any],
        thread_id: str | None = None,
        max_retries: int = 2,
    ) -> dict[str, Any]:
        tid = thread_id or uuid.uuid4().hex[:12]

        # Checkpoint provides continuity, caller state provides new values.
        # Merge saved state under the new state — new keys win. This lets a
        # new turn with the same thread_id update state without losing what
        # previous runs produced. (resume() already loads + enriches state
        # before calling invoke and sets _resume_from.)
        if self._checkpointer and not state.get("_resume_from"):
            saved = await self._checkpointer.load(tid)
            if saved:
                state = {**saved, **state}
                logger.info("[kitegen] Restored state: %s", tid)

        state["_thread_id"] = tid
        state.setdefault("_node_history", [])

        current = state.get("_resume_from") or self._entry
        state.pop("_resume_from", None)

        while current is not None:
            node_fn = self._nodes.get(current)
            if node_fn is None:
                raise ValueError(f"Node '{current}' not found")

            trace = NodeTrace(node=current, started_at=time.time())
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

            current = await self._next(current, state)

        state["_completed"] = True
        if self._checkpointer:
            await self._checkpointer.save(state, tid)
        return state

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

        # Same merge semantics as invoke(): checkpoint as base, new keys win
        if self._checkpointer and not state.get("_resume_from"):
            saved = await self._checkpointer.load(tid)
            if saved:
                state = {**saved, **state}
                logger.info("[kitegen] Restored state: %s", tid)

        state["_thread_id"] = tid
        state.setdefault("_node_history", [])

        current: str | None = state.get("_resume_from") or self._entry
        state.pop("_resume_from", None)

        q: asyncio.Queue = asyncio.Queue()
        token_q = _stream_queue.set(q)
        # Capture the generator's context now — when the caller closes the
        # stream early, the finally block runs in the caller's context and
        # ContextVar tokens must be reset in the context that created them.
        _cleanup_ctx = contextvars.copy_context()
        node_task: asyncio.Task | None = None
        getter: asyncio.Task | None = None

        def _reset_stream_var() -> None:
            _stream_queue.reset(token_q)

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
                try:
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
                        getter = None
                finally:
                    # Stream closed early (client stopped) or an error
                    # occurred — cancel the in-flight node task and the
                    # pending queue getter. Any in-flight exception
                    # (GeneratorExit or otherwise) continues propagating
                    # after this block.
                    if getter is not None and not getter.done():
                        getter.cancel()
                    if node_task is not None and not node_task.done():
                        node_task.cancel()
                        try:
                            node_task.result()
                        except (asyncio.CancelledError, Exception):
                            pass

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
            # Cleanup on any exit — also catches GeneratorExit from the
            # caller closing the stream mid-node
            if node_task is not None and not node_task.done():
                node_task.cancel()
                try:
                    node_task.result()
                except (asyncio.CancelledError, Exception):
                    pass
            try:
                _cleanup_ctx.run(_reset_stream_var)
            except ValueError:
                pass  # context already torn down

    async def resume(self, data: Any, thread_id: str) -> dict[str, Any]:
        """Resume a graph paused by interrupt().

        The resume data is returned from the interrupt() call inside the node.
        Re-runs the interrupted node — the node is responsible for checking
        whether it already processed the data (e.g. via state.get('done')).
        """
        if self._checkpointer:
            state = await self._checkpointer.load(thread_id)
        else:
            raise ValueError("resume() requires a checkpointer")

        if not state:
            raise ValueError(f"No state found for thread '{thread_id}'")
        if not state.get("_interrupted_at"):
            raise ValueError("Graph was not interrupted. Nothing to resume.")

        # Mark that we should re-run the interrupted node
        state["_resume_from"] = state.pop("_interrupted_at")

        # Pass the resume data — accessible to the node
        state["_resume_data"] = data

        return await self.invoke(state, thread_id=thread_id)

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

    async def _next(self, current: str, state: dict) -> str | None:
        if current in self._conditional:
            router, mapping = self._conditional[current]
            key = await router(state) if asyncio.iscoroutinefunction(router) else router(state)
            return mapping.get(key)
        return self._edges.get(current)


