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


async def test_to_worker_callable_interval():
    """A callable interval is re-evaluated after every run.

    First wait is deliberately huge (10s): if the interval were evaluated
    only once at startup, the worker would sleep 10s after the first run
    and the 5s timeout would fail the test. Re-evaluation makes subsequent
    waits tiny, completing quickly.
    """
    calls = []
    stop = asyncio.Event()

    async def task():
        calls.append(len(calls))
        if len(calls) >= 3:
            stop.set()

    def interval():
        return 10.0 if len(calls) == 0 else 0.01

    await asyncio.wait_for(
        kg.to_worker(task, interval=interval, stop_event=stop), timeout=5
    )
    assert len(calls) >= 3


async def test_to_worker_interval_callable_failure_falls_back():
    """A raising interval callable logs and falls back — worker survives."""
    calls = []
    stop = asyncio.Event()

    async def task():
        calls.append(len(calls))
        if len(calls) >= 2:
            stop.set()

    def broken_interval():
        raise ValueError("corrupt config")

    # First run happens immediately; then the broken interval is hit,
    # the fallback (300s) is logged, but the stop_event fires on the
    # second run — cancel instead of waiting 300s
    worker = asyncio.create_task(
        kg.to_worker(task, interval=broken_interval, stop_event=stop)
    )
    await asyncio.sleep(0.1)
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass
    assert len(calls) >= 1  # the task ran before the interval broke
