"""kitegen.deploy — One-liner deployment: run any async task as a worker.

Usage:
    import asyncio
    import kitegen as kg

    async def my_task():
        print("checking...")

    # Run my_task() every 5 minutes forever
    await kg.to_worker(my_task, interval=300)

    # With a stop event
    stop = asyncio.Event()
    worker = asyncio.create_task(kg.to_worker(my_task, interval=300, stop_event=stop))
    ...
    stop.set()
    await worker
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger("kitegen")


async def to_worker(
    task: Callable[[], Awaitable[object]],
    *,
    interval: float | Callable[[], float] = 300.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run ``task()`` repeatedly every ``interval`` seconds until cancelled.

    The task runs immediately on start, then sleeps ``interval`` between runs.
    Exceptions in the task are logged and do not stop the loop — the worker
    keeps retrying on the next tick. Set ``stop_event`` (or cancel the
    returned coroutine's task) to stop.

    Args:
        task: Async callable taking no arguments.
        interval: Seconds between runs, or a zero-arg callable returning the
            seconds. A callable is re-evaluated after every run — config
            changes take effect without restarting the worker. Default 300.
        stop_event: Optional event; when set, the worker exits cleanly.
    """
    while True:
        try:
            await task()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[kitegen] worker task failed — retrying at next tick")

        try:
            wait = interval() if callable(interval) else interval
        except Exception:
            # A config-driven interval callable that raises (corrupt config
            # file, bad value) must not kill the worker silently
            logger.exception("[kitegen] worker interval callable failed — using 300s fallback")
            wait = 300.0

        if stop_event is not None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=wait)
                return
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(wait)


__all__ = ["to_worker"]
