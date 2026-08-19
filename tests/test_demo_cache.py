"""Tests for demo.cache — research report caching."""

import json
from datetime import datetime, timedelta

from demo import cache


async def test_cache_roundtrip_and_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache, "TTL_HOURS", 4.0)

    # Miss on empty cache
    assert cache.get_cached_research("AAPL") is None

    # Store then hit
    cache.cache_research("AAPL", "Apple research report")
    cached = cache.get_cached_research("AAPL")
    assert cached is not None
    assert cached["report"] == "Apple research report"
    assert cached["symbol"] == "AAPL"

    # TTL 0 → everything stale
    monkeypatch.setattr(cache, "TTL_HOURS", 0.0)
    assert cache.get_cached_research("AAPL") is None

    # Invalidate removes the file
    cache.cache_research("TSLA", "Tesla report")
    cache.invalidate("TSLA")
    assert cache.get_cached_research("TSLA") is None


async def test_cache_survives_corrupt_file(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)

    (tmp_path / "research_AAPL.json").write_text("{corrupt json")
    assert cache.get_cached_research("AAPL") is None


async def test_cache_symbol_key_sanitized(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)

    cache.cache_research("0700.HK", "Tencent report")
    assert (tmp_path / "research_0700_HK.json").exists()


async def test_cache_concurrent_writers_never_corrupt(monkeypatch, tmp_path):
    """Concurrent writers of the same symbol — the file must always parse."""
    import threading

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)

    errors = []

    def writer(thread_id: int) -> None:
        for i in range(25):
            cache.cache_research("AAPL", f"report from thread {thread_id} #{i}")

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    cached = cache.get_cached_research("AAPL")
    assert cached is not None
    assert cached["report"].startswith("report from thread ")
    assert cached["symbol"] == "AAPL"
