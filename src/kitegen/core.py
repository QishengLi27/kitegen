"""kitegen.core — Shared abstractions for executables, context, events, and retries.

This module defines the unifying protocol that every kitegen component implements:
Agent, Task, Crew, Graph, and plain functions are all Executable.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("kitegen")

# ── Retry policy ───────────────────────────────────────────────────────────


@dataclass
class RetryPolicy:
    """Retry configuration for node/tool execution."""

    max_retries: int = 2
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential: bool = True

    def delay_for(self, attempt: int) -> float:
        """Return the delay before retry attempt `attempt` (0-indexed)."""
        if self.exponential:
            delay = self.base_delay * (2 ** attempt)
        else:
            delay = self.base_delay
        return min(delay, self.max_delay)


# ── Context ────────────────────────────────────────────────────────────────


@dataclass
class Context:
    """Runtime services available to every Executable.

    A Context is created once per run and passed through every executable.
    It carries checkpointing, streaming, interrupt, and retry infrastructure.
    """

    thread_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    node_name: str | None = None
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    interrupt_payload: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # These are set by the runtime (Agent, Graph, etc.) when streaming/checkpointing
    _stream_queue: asyncio.Queue[Any] | None = field(default=None, repr=False)
    _checkpointer: Any | None = field(default=None, repr=False)

    def stream(self, event: Any) -> None:
        """Emit an event into the active stream, if any."""
        if self._stream_queue is not None:
            try:
                self._stream_queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("[kitegen] Stream queue full, dropping event")

    def copy(self, **overrides: Any) -> Context:
        """Return a shallow copy with optional overrides."""
        return Context(
            thread_id=overrides.get("thread_id", self.thread_id),
            node_name=overrides.get("node_name", self.node_name),
            retry_policy=overrides.get("retry_policy", self.retry_policy),
            interrupt_payload=overrides.get("interrupt_payload", self.interrupt_payload),
            metadata=overrides.get("metadata", dict(self.metadata)),
            _stream_queue=overrides.get("_stream_queue", self._stream_queue),
            _checkpointer=overrides.get("_checkpointer", self._checkpointer),
        )


# ── Executable Protocol ────────────────────────────────────────────────────


@runtime_checkable
class Executable(Protocol):
    """Protocol for anything that can transform state."""

    async def execute(self, state: dict[str, Any], context: Context) -> dict[str, Any]:
        ...


# ── Runnable mixin ───────────────────────────────────────────────────────────


class Runnable(ABC):
    """Base class that provides convenient `run()` and `stream()` methods.

    Subclasses must implement `execute(state, context)`.
    """

    @abstractmethod
    async def execute(self, state: dict[str, Any], context: Context) -> dict[str, Any]:
        raise NotImplementedError

    async def run(
        self,
        state: dict[str, Any] | None = None,
        *,
        thread_id: str | None = None,
        context: Context | None = None,
    ) -> dict[str, Any]:
        """Execute with a fresh or supplied context."""
        ctx = context or Context(thread_id=thread_id or uuid.uuid4().hex[:12])
        state = dict(state or {})
        state["_thread_id"] = ctx.thread_id
        return await self.execute(state, ctx)

    async def stream(
        self,
        state: dict[str, Any] | None = None,
        *,
        thread_id: str | None = None,
        context: Context | None = None,
    ) -> AsyncIterator[Any]:
        """Execute and yield stream events. Final event is the returned state."""
        ctx = context or Context(thread_id=thread_id or uuid.uuid4().hex[:12])
        q: asyncio.Queue[Any] = asyncio.Queue()
        ctx = ctx.copy(_stream_queue=q)

        task = asyncio.ensure_future(self.execute(state or {}, ctx))
        try:
            while True:
                getter = asyncio.ensure_future(q.get())
                done, pending = await asyncio.wait(
                    [task, getter], return_when=asyncio.FIRST_COMPLETED
                )
                if getter in done:
                    yield getter.result()
                if getter in pending:
                    getter.cancel()
                if task.done():
                    break
            # Drain remaining events
            while not q.empty():
                yield q.get_nowait()
            # Yield the final state as a result
            result_state = await task
            yield Complete()
            yield result_state
        finally:
            if not task.done():
                task.cancel()


# ── Function wrapper ─────────────────────────────────────────────────────────


class FunctionExecutable(Runnable):
    """Wraps a sync or async callable as an Executable."""

    def __init__(self, fn: Callable[..., Awaitable[dict[str, Any]] | dict[str, Any]]):
        self.fn = fn
        self._name = getattr(fn, "__name__", "function")

    async def execute(self, state: dict[str, Any], context: Context) -> dict[str, Any]:
        result = self.fn(state, context)
        if asyncio.iscoroutine(result):
            return await result
        return result


def as_executable(fn: Any) -> Runnable:
    """Convert a callable or Executable into a Runnable."""
    if isinstance(fn, Runnable):
        return fn
    if isinstance(fn, Executable):
        # Protocol runtime_checkable only checks execute method presence
        return _ExecutableWrapper(fn)
    if callable(fn):
        return FunctionExecutable(fn)
    raise TypeError(f"Cannot convert {type(fn)} to Executable")


class _ExecutableWrapper(Runnable):
    """Wraps an object that implements the Executable protocol but isn't a Runnable."""

    def __init__(self, executable: Executable):
        self._executable = executable

    async def execute(self, state: dict[str, Any], context: Context) -> dict[str, Any]:
        return await self._executable.execute(state, context)


# ── Events ───────────────────────────────────────────────────────────────────


@dataclass
class NodeTrace:
    """Record of a single executable/node execution."""

    node: str
    started_at: float
    finished_at: float = 0.0
    error: str | None = None


@dataclass
class NodeStart:
    """Emitted when a node/executable begins execution."""

    node: str


@dataclass
class NodeEnd:
    """Emitted when a node/executable completes successfully."""

    node: str
    trace: NodeTrace | None = None


@dataclass
class NodeError:
    """Emitted when a node/executable fails."""

    node: str
    error: str


@dataclass
class ToolCallEvent:
    """Emitted when a tool is invoked."""

    node: str
    tool: str
    arguments: dict[str, Any]


@dataclass
class ToolResultEvent:
    """Emitted when a tool returns a result."""

    node: str
    tool: str
    result: Any


@dataclass
class TokenEvent:
    """Emitted when an LLM streams a token."""

    node: str
    content: str


@dataclass
class Interrupt:
    """Emitted when execution pauses for human input."""

    node: str
    payload: Any


@dataclass
class Complete:
    """Emitted when execution finishes."""

    pass


@dataclass
class Custom:
    """Emitted for arbitrary custom stream data from a node/executable."""

    node: str
    data: Any


# ── Exceptions ─────────────────────────────────────────────────────────────


class KitegenError(Exception):
    """Base exception for kitegen."""

    pass


class LLMRetryableError(KitegenError):
    """Raise inside a node/tool to trigger automatic retry."""

    pass


class InterruptError(KitegenError):
    """Raised internally when an executable requests human input."""

    def __init__(self, payload: Any):
        self.payload = payload


async def interrupt(payload: Any = None) -> Any:
    """Pause execution and return `payload` to the caller.

    Call this inside any executable. Resume with the runner's resume method
    or by re-running with `_interrupt_payload` set in context.
    """
    raise InterruptError(payload)


# ── Utility ──────────────────────────────────────────────────────────────────


async def execute_with_retry(
    fn: Callable[..., Awaitable[Any]],
    *args: Any,
    context: Context,
    **kwargs: Any,
) -> Any:
    """Execute `fn(*args, **kwargs)` with retry on LLMRetryableError."""
    policy = context.retry_policy
    last_error: Exception | None = None
    for attempt in range(policy.max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except LLMRetryableError as e:
            last_error = e
            if attempt == policy.max_retries:
                break
            delay = policy.delay_for(attempt)
            logger.warning(
                "[kitegen] Retry %d/%d after %.1fs: %s",
                attempt + 1,
                policy.max_retries,
                delay,
                e,
            )
            await asyncio.sleep(delay)
    raise last_error or RuntimeError("execute_with_retry failed without error")
