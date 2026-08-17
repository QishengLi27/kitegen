"""demo.monitor — Market monitoring engine.

Monitors three event types:
1. Stop-loss / take-profit triggers — position price crosses user-set levels
2. Daily price moves — position moves beyond threshold (default 5%)
3. Daily market briefing — generated each trading day after 08:50

Alert dedup: a condition that stays true only alerts once; it re-arms
when the condition clears.

Notification channels:
- Console log (always)
- Webhook (set WEBHOOK_URL env var, POSTs JSON {"text": ...})
- Persisted to data/alerts.json (read by the frontend panel)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import kitegen as kg
from demo.portfolio import load_portfolio
from demo.tools import fetch_stock

logger = logging.getLogger("kitegen.monitor")

DATA_DIR = Path(__file__).parent / "data"
ALERTS_FILE = DATA_DIR / "alerts.json"
STATE_FILE = DATA_DIR / "monitor_state.json"

# Config (env var overridable)
MOVE_THRESHOLD = float(os.getenv("MONITOR_MOVE_THRESHOLD", "5"))   # daily move %
CHECK_INTERVAL = float(os.getenv("MONITOR_INTERVAL", "300"))       # check interval seconds
BRIEFING_TIME = os.getenv("MONITOR_BRIEFING_TIME", "08:50")        # briefing time
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")                         # notification webhook


@dataclass
class Alert:
    id: str
    timestamp: str
    kind: str          # stop_loss | take_profit | price_move | briefing
    symbol: str
    price: float
    message: str
    acknowledged: bool = False


# ── Persistence ──────────────────────────────────────────────────────────────


def _load_json(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default
    return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_alerts() -> list[dict]:
    return _load_json(ALERTS_FILE, [])


def _append_alert(alert: Alert) -> None:
    alerts = load_alerts()
    alerts.insert(0, asdict(alert))
    _save_json(ALERTS_FILE, alerts[:100])  # keep the latest 100


# ── Alert dedup state ────────────────────────────────────────────────────────


def _get_state() -> dict:
    return _load_json(STATE_FILE, {})


def _set_state(state: dict) -> None:
    _save_json(STATE_FILE, state)


def _armed(state: dict, key: str) -> bool:
    """False means the condition is still true since last alert — don't repeat."""
    return not state.get(key, False)


# ── Notify ───────────────────────────────────────────────────────────────────


def _notify(message: str) -> None:
    logger.info("[monitor] %s", message)
    if WEBHOOK_URL:
        try:
            import requests

            requests.post(WEBHOOK_URL, json={"text": message}, timeout=10)
        except Exception as e:
            logger.warning("[monitor] webhook failed: %s", e)


# ── Check logic ──────────────────────────────────────────────────────────────


def _fetch_all(symbols: list[str], timeout: float = 15.0) -> dict[str, dict | None]:
    """Fetch quotes for all symbols in parallel, 15s cap each
    (yahooquery hangs on this network)."""
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FutTimeout

    results: dict[str, dict | None] = {}
    with ThreadPoolExecutor(max_workers=len(symbols) or 1) as ex:
        futures = {ex.submit(fetch_stock, s): s for s in symbols}
        for fut, symbol in futures.items():
            try:
                results[symbol] = fut.result(timeout=timeout)
            except FutTimeout:
                logger.warning("[monitor] fetch %s timed out", symbol)
                results[symbol] = None
            except Exception as e:
                logger.warning("[monitor] fetch %s failed: %s", symbol, e)
                results[symbol] = None
    return results


def check_alerts(portfolio_id: str = "default") -> list[Alert]:
    """Check all alert conditions across the portfolio. Returns newly triggered alerts."""
    portfolio = load_portfolio(portfolio_id)
    state = _get_state()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_alerts: list[Alert] = []

    prices = _fetch_all(list(portfolio.positions.keys()))
    for symbol, pos in portfolio.positions.items():
        data = prices.get(symbol)
        if not data:
            logger.warning("[monitor] no data for %s, skipping", symbol)
            continue

        price = data["price"]
        chg = data.get("chg") or 0

        # 1. Stop-loss trigger
        if pos.stop_loss and price <= pos.stop_loss:
            key = f"{symbol}:stop_loss"
            if _armed(state, key):
                pnl_pct = round((price - pos.cost_basis) / pos.cost_basis * 100, 1)
                new_alerts.append(Alert(
                    id=uuid.uuid4().hex[:10],
                    timestamp=now,
                    kind="stop_loss",
                    symbol=symbol,
                    price=price,
                    message=(
                        f"STOP LOSS TRIGGERED: {symbol} at {price}, below stop {pos.stop_loss}.\n"
                        f"Position: {pos.shares} shares @ {pos.cost_basis} cost, {pnl_pct}% unrealized.\n"
                        f"Action: sell per plan."
                    ),
                ))
            state[key] = True
        elif pos.stop_loss:
            state[f"{symbol}:stop_loss"] = False  # condition cleared, re-arm

        # 2. Take-profit trigger
        if pos.take_profit and price >= pos.take_profit:
            key = f"{symbol}:take_profit"
            if _armed(state, key):
                pnl_pct = round((price - pos.cost_basis) / pos.cost_basis * 100, 1)
                new_alerts.append(Alert(
                    id=uuid.uuid4().hex[:10],
                    timestamp=now,
                    kind="take_profit",
                    symbol=symbol,
                    price=price,
                    message=(
                        f"TAKE PROFIT HIT: {symbol} at {price}, target {pos.take_profit}.\n"
                        f"Position: {pos.shares} shares, +{pnl_pct}% unrealized.\n"
                        f"Action: take profit or trail the stop higher."
                    ),
                ))
            state[key] = True
        elif pos.take_profit:
            state[f"{symbol}:take_profit"] = False

        # 3. Daily move alert
        if abs(chg) >= MOVE_THRESHOLD:
            key = f"{symbol}:move"
            if _armed(state, key):
                direction = "up" if chg > 0 else "down"
                new_alerts.append(Alert(
                    id=uuid.uuid4().hex[:10],
                    timestamp=now,
                    kind="price_move",
                    symbol=symbol,
                    price=price,
                    message=(
                        f"BIG MOVE: {symbol} {direction} {abs(chg)}% today (price {price}).\n"
                        f"Review position sizing and stop levels."
                    ),
                ))
            state[key] = True
        else:
            state[f"{symbol}:move"] = False

    _set_state(state)
    for alert in new_alerts:
        _append_alert(alert)
        _notify(alert.message)
    return new_alerts


# ── Daily briefing ───────────────────────────────────────────────────────────

_briefing_lock = asyncio.Lock()


async def generate_briefing(force: bool = False) -> Alert | None:
    """Generate today's market briefing via portfolio_analyst.

    Deduped by date: returns None if today's briefing already exists and
    force is False. Auto schedule and manual trigger share this function;
    the lock guarantees concurrent triggers produce only one briefing.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    async with _briefing_lock:
        # Dedup: check file for an existing briefing today (works across restarts)
        if not force:
            for alert in load_alerts():
                if alert.get("kind") == "briefing" and alert.get("timestamp", "").startswith(today):
                    logger.info("[monitor] briefing already generated today, skipping")
                    return None

        from demo.agents import portfolio_analyst

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result = await portfolio_analyst.run({
            "input": (
                "Generate today's market briefing: list all my positions with "
                "latest price, key support/resistance levels, and points to watch "
                "today (including any stop-loss/take-profit levels already set). "
                "Keep it concise, use tables."
            ),
        })

        alert = Alert(
            id=uuid.uuid4().hex[:10],
            timestamp=now,
            kind="briefing",
            symbol="ALL",
            price=0.0,
            message=result.get("output", ""),
        )
        _append_alert(alert)
        _notify(f"DAILY MARKET BRIEFING\n\n{alert.message}")
        return alert


# ── Monitor main loop ────────────────────────────────────────────────────────


async def _monitor_tick() -> None:
    """One check pass: alerts + time-of-day briefing."""
    now = datetime.now()
    try:
        # check_alerts is sync (requests lib) — run in a thread to keep the
        # event loop free
        triggered = await asyncio.to_thread(check_alerts)
        if triggered:
            logger.info("[monitor] %d alert(s) triggered", len(triggered))

        # Briefing: once past BRIEFING_TIME, the next tick generates it
        # (generate_briefing dedups by date internally)
        if now.strftime("%H:%M") >= BRIEFING_TIME:
            logger.info("[monitor] checking morning briefing...")
            await generate_briefing()
    except Exception:
        logger.exception("[monitor] tick failed")


async def start_monitor() -> None:
    """Start the monitoring worker (called by backend on startup)."""
    logger.info(
        "[monitor] starting — interval=%ss, move_threshold=%s%%, briefing=%s",
        CHECK_INTERVAL, MOVE_THRESHOLD, BRIEFING_TIME,
    )
    await kg.to_worker(_monitor_tick, interval=CHECK_INTERVAL)
