"""demo.cache — Research report cache.

Separates the slow, cacheable research report from the real-time advice
stage. A research report (company picture, trend regime) changes on a
timescale of hours; advice changes with every tick. Cache the former,
always compute the latter with fresh data.

Cache entries live in data/cache/research_<SYMBOL>.json (gitignored).
TTL is configurable via RESEARCH_CACHE_TTL_HOURS (default 4).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

CACHE_DIR = Path(__file__).parent / "data" / "cache"
TTL_HOURS = float(os.getenv("RESEARCH_CACHE_TTL_HOURS", "4"))

# The chat pipeline and the paper trader can generate research for the
# same symbol concurrently — serialize file access and write atomically
# so a torn write can never corrupt a cache entry.
_lock = threading.Lock()


def _cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"research_{symbol.replace('.', '_')}.json"


def get_cached_research(symbol: str) -> dict | None:
    """Return the cached research report for a symbol, or None if absent/stale."""
    path = _cache_path(symbol)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        generated_at = datetime.fromisoformat(data["generated_at"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return None

    age = datetime.now() - generated_at
    if age > timedelta(hours=TTL_HOURS):
        return None  # stale

    return data


def cache_research(symbol: str, report: str) -> None:
    """Store a freshly generated research report.

    Atomic write (temp file + os.replace): a concurrent reader can never
    observe a half-written file.
    """
    with _lock:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "symbol": symbol,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "report": report,
        }
        path = _cache_path(symbol)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)


def invalidate(symbol: str) -> None:
    """Drop a symbol's cached report (force refresh)."""
    _cache_path(symbol).unlink(missing_ok=True)
