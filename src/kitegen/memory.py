"""kitegen.memory — Pluggable conversation memory for agents.

Same pattern as LLMAdapter: a protocol, one simple built-in, bring your own.

Usage:
    agent = kg.Agent(
        role="trader",
        goal="...",
        memory=kg.BufferMemory(size=8),   # keeps the last 4 exchanges
        llm=...,
    )

Each run, previous exchanges are injected between the system prompt and
the current user message. After the run, the new exchange is recorded.
Memory is in-process — for persistence across restarts, implement the
protocol with a file/database backend.
"""

from __future__ import annotations

from typing import Protocol

from kitegen.llm import Message


class Memory(Protocol):
    """Protocol for agent memory backends.

    Implementations must provide add/get/clear/__len__ — kitegen internals
    (e.g. memory rebuilds) rely on all four.
    """

    def add(self, role: str, content: str) -> None:
        """Record one message."""
        ...

    def get(self) -> list[Message]:
        """Return the recorded messages (oldest first)."""
        ...

    def clear(self) -> None:
        """Drop all recorded messages."""
        ...

    def __len__(self) -> int:
        """Number of recorded messages."""
        ...


class BufferMemory:
    """Sliding-window memory — keeps the most recent ``size`` messages."""

    def __init__(self, size: int = 10):
        self.size = size
        self._messages: list[Message] = []

    def add(self, role: str, content: str) -> None:
        self._messages.append(Message(role=role, content=content))
        if len(self._messages) > self.size:
            self._messages = self._messages[-self.size:]

    def get(self) -> list[Message]:
        return list(self._messages)

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)


__all__ = ["Memory", "BufferMemory"]
