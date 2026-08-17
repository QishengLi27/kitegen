"""kitegen.resilience — Circuit breaker, retry, cost tracking.

Usage:
    cb = CircuitBreaker("my-llm", failure_threshold=3, recovery_timeout=60)
    result = await cb.call(my_async_fn, arg1, arg2)

    tracker = TokenTracker()
    tracker.record("deepseek-chat", usage)
    tracker.summary()  # {"total_cost": 0.0042, "total_tokens": 3990}
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from kitegen.llm import Usage


# ── Token Tracker ──────────────────────────────────────────────────────

@dataclass
class TokenTracker:
    """Accumulate usage and cost across multiple LLM calls."""

    _records: list[dict] = field(default_factory=list)

    def record(self, model: str, usage: Usage) -> None:
        self._records.append({
            "model": model,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "cost": usage.cost(model),
        })

    def total_tokens(self) -> int:
        return sum(r["total_tokens"] for r in self._records)

    def total_cost(self) -> float:
        return sum(r["cost"] for r in self._records)

    def by_model(self) -> dict[str, dict]:
        models = {}
        for r in self._records:
            m = r["model"]
            if m not in models:
                models[m] = {"calls": 0, "tokens": 0, "cost": 0.0}
            models[m]["calls"] += 1
            models[m]["tokens"] += r["total_tokens"]
            models[m]["cost"] += r["cost"]
        return models

    def summary(self) -> dict:
        return {
            "total_tokens": self.total_tokens(),
            "total_cost": round(self.total_cost(), 6),
            "by_model": self.by_model(),
        }

    def reset(self) -> None:
        self._records.clear()

    def to_dict(self) -> list[dict]:
        """Export records as a plain list of dicts (JSON-serializable)."""
        return [dict(r) for r in self._records]

    def load_records(self, records: list[dict]) -> None:
        """Replace current records with previously exported ones.

        Combined with to_dict(), this gives persistence across process
        restarts: save to a file, reload on startup.
        """
        self._records = [dict(r) for r in records]


# ── Circuit Breaker ─────────────────────────────────────────────────────

class CircuitBreaker:
    """Fail-fast pattern: after N consecutive failures, open for T seconds.

    Usage:
        cb = CircuitBreaker("llm-api", failure_threshold=3, recovery_timeout=60)
        result = await cb.call(my_async_fn, *args, **kwargs)
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
    ):
        self.name = name
        self._threshold = failure_threshold
        self._timeout = recovery_timeout
        self._failures = 0
        self._opened_at: float = 0.0

    @property
    def is_open(self) -> bool:
        if self._failures < self._threshold:
            return False
        if time.time() - self._opened_at > self._timeout:
            # Recovery window passed — try one call (half-open)
            self._failures = 0
            return False
        return True

    async def call(
        self,
        fn: Callable[..., Awaitable[Any]],
        *args,
        fallback: Callable[..., Any] | None = None,
        **kwargs,
    ) -> Any:
        """Execute fn. If circuit is open, return fallback() or raise."""
        if self.is_open:
            if fallback:
                return fallback(*args, **kwargs) if asyncio.iscoroutinefunction(fallback) else fallback(*args, **kwargs)
            raise CircuitOpenError(f"Circuit '{self.name}' is open")

        try:
            result = await fn(*args, **kwargs)
            self._failures = 0  # Reset on success
            return result
        except Exception:
            self._failures += 1
            if self._failures >= self._threshold:
                self._opened_at = time.time()
            raise


class CircuitOpenError(Exception):
    pass
