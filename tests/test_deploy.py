"""Tests for kitegen.deploy — to_worker scheduling."""

import asyncio

import kitegen as kg


async def test_to_worker_runs_repeatedly():
    """to_worker runs the task immediately and then on each interval."""
    calls = []
    stop = asyncio.Event()

    async def task():
        calls.append(len(calls))
        if len(calls) >= 3:
            stop.set()

    await asyncio.wait_for(kg.to_worker(task, interval=0.01, stop_event=stop), timeout=5)
    assert len(calls) >= 3


async def test_to_worker_survives_task_errors():
    """A failing task does not stop the worker."""
    calls = []
    stop = asyncio.Event()

    async def flaky():
        calls.append(len(calls))
        if len(calls) == 1:
            raise RuntimeError("boom")
        if len(calls) >= 3:
            stop.set()

    await asyncio.wait_for(kg.to_worker(flaky, interval=0.01, stop_event=stop), timeout=5)
    assert len(calls) >= 3  # kept going after the error


async def test_to_worker_cancellation():
    """Cancelling the worker task stops the loop."""
    calls = []

    async def task():
        calls.append(len(calls))

    worker = asyncio.create_task(kg.to_worker(task, interval=3600))
    await asyncio.sleep(0.05)
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass
    # Ran at least once (immediately on start), then cancelled
    assert len(calls) >= 1
