"""demo.paper_engine — Paper trading engine.

One tick:
  1. Fetch prices + compute_signal for the universe
  2. The decision agent proposes trades (structured output — TradeDecisions)
  3. Rule enforcement: position caps, cash checks, T+1, forced stop-loss
  4. Execute simulated trades, snapshot the equity curve, persist

The decision agent tests the AGENT's trading ability; the rule layer
guarantees the agent can never violate hard constraints. The agent uses
kitegen's structured output (F7) so decisions are machine-typed, and a
BufferMemory (F6) so it remembers its own prior decisions across ticks.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import kitegen as kg
from demo.agents import _get_llm, market_researcher
from demo.cache import cache_research, get_cached_research
from demo.monitor import _fetch_all
from demo.paper import (
    PaperAccount,
    PaperConfig,
    get_universe,
    is_symbol_tradable,
    is_trading_time,
    load_config,
)
from demo.tools import compute_signal

try:
    from pydantic import BaseModel, Field
except ImportError as _e:  # pydantic ships with fastapi in the demo
    raise ImportError(
        "paper_engine requires pydantic (nested TradeDecisions validation). "
        "Install with: pip install pydantic"
    ) from _e

logger = logging.getLogger("kitegen.paper")


# ── Structured decision types ────────────────────────────────────────────────


class TradeDecision(BaseModel):
    symbol: str
    action: str = Field(description='one of "buy" | "sell" | "hold"')
    shares: int = Field(default=0, description="0 on sell = entire position")
    reason: str = Field(default="", description="one-sentence rationale")


class TradeDecisions(BaseModel):
    decisions: list[TradeDecision] = Field(
        description="one decision per symbol under consideration"
    )


# ── Decision agent ───────────────────────────────────────────────────────────

DECISION_SYSTEM_PROMPT = """You are a disciplined quantitative trader managing a paper trading account.

You receive the account state and technical signals for a set of symbols.
Decide the trades for THIS tick. Output STRICT JSON only — no markdown, no
commentary — exactly in this shape:

{"decisions": [
  {"symbol": "600519.SS", "action": "buy", "shares": 100, "reason": "one sentence"},
  {"symbol": "AAPL", "action": "sell", "shares": 0, "reason": "one sentence"}
]}

Rules:
- action is exactly one of "buy" | "sell" | "hold"
- For "sell", shares=0 means sell the entire position; the engine clamps to held quantity
- For "buy", shares must respect cash and position caps — the engine rejects oversized buys
- T+1 is enforced by the engine; do not avoid selling only because of it
- Only trade on clear signals. "hold" is a valid decision — do not force trades
- Be risk-aware: cut losses, let winners run, never chase
"""

paper_trader = kg.Agent(
    role="paper trading decision maker",
    goal=(
        "Maximize risk-adjusted returns of a paper trading account. "
        "Make one decision set per tick based on the signals provided. "
        "Prefer high-conviction setups; hold when nothing is clear."
    ),
    personality="Disciplined, risk-aware, decisive. Never overtrade.",
    system_prompt=DECISION_SYSTEM_PROMPT,
    tools=[],  # data is precomputed and injected; decisions only
    llm=_get_llm(),
    max_iterations=1,
    output_schema=TradeDecisions,          # F7: machine-typed decisions
    memory=kg.BufferMemory(size=6),        # F6: remembers the last 3 ticks
)


# ── Research reports ─────────────────────────────────────────────────────────

# Research text is long — the decision prompt carries a digest per symbol
RESEARCH_DIGEST_CHARS = 500


async def _ensure_research(prices: dict[str, float]) -> dict[str, str]:
    """Return a research digest per symbol.

    Cached reports within TTL are reused (shared cache with the chat
    assistant). On miss or stale, market_researcher generates a fresh
    report which is stored back into the cache.
    """
    digests: dict[str, str] = {}
    for symbol in prices:
        cached = get_cached_research(symbol)
        if cached:
            report = cached["report"]
            logger.info("[paper] research cache hit for %s (%s)", symbol, cached["generated_at"])
        else:
            try:
                result = await market_researcher.execute({
                    "input": (
                        f"Analyze {symbol} for a trading decision. Cover: "
                        f"trend regime (bull/bear/range), key support/resistance "
                        f"levels, and the main risks. Be concise — 250 words max."
                    ),
                })
                report = result.get("output", "")
                if report:
                    try:
                        cache_research(symbol, report)
                    except OSError:
                        pass  # cache is best-effort
                else:
                    report = "(research unavailable)"
                logger.info("[paper] generated research for %s (%d chars)", symbol, len(report))
            except Exception as e:
                logger.warning("[paper] research failed for %s: %s", symbol, e)
                report = "(research unavailable)"

        digests[symbol] = (
            report[:RESEARCH_DIGEST_CHARS]
            + ("…" if len(report) > RESEARCH_DIGEST_CHARS else "")
        )
    return digests


# ── Tick ─────────────────────────────────────────────────────────────────────

_tick_lock = asyncio.Lock()


async def paper_tick(force: bool = False) -> dict:
    """Run one check-and-trade pass. Returns a summary of what happened.

    With ``force=False`` (default), ticks outside trading hours are skipped
    (config.trading_hours_only). Pass ``force=True`` to bypass — useful for
    manual testing.

    Serialized by a process-wide lock: manual /paper/tick and the background
    worker can overlap, and concurrent ticks would corrupt the account
    (load → mutate → save interleaving).
    """
    async with _tick_lock:
        return await _paper_tick_locked(force)


async def _paper_tick_locked(force: bool = False) -> dict:
    config = load_config()

    if config.trading_hours_only and not force:
        in_session, why = is_trading_time()
        if not in_session:
            logger.info("[paper] tick skipped: %s", why)
            return {"status": "skipped", "reason": why}

        # Universe-aware gate: the global check passed because SOME market
        # is open, but if no symbol in THIS universe is tradable right now
        # (e.g. an A-share-only universe during US hours), skip the whole
        # tick before spending any LLM calls.
        universe = get_universe(config)
        tradable_symbols = [
            s for s in universe if is_symbol_tradable(s)[0]
        ]
        if universe and not tradable_symbols:
            closed = ", ".join(
                f"{s} ({is_symbol_tradable(s)[1]})" for s in universe
            )
            logger.info("[paper] tick skipped: all universe symbols closed — %s", closed)
            return {"status": "skipped", "reason": f"all symbols' markets closed: {closed}"}

    account = PaperAccount.load()
    universe = get_universe(config)

    # 1. Fresh data: prices + signals (deterministic tools, no LLM)
    fetched = _fetch_all(universe)
    prices = {s: d["price"] for s, d in fetched.items() if d}
    signals = {}
    for symbol in universe:
        if symbol not in prices:
            continue
        result = await compute_signal.invoke({"symbol": symbol}, kg.Context())
        # Full signal text — indicators, ATR key levels, and the vote summary
        # all inform the decision
        signals[symbol] = result.strip()

    if not prices:
        logger.warning("[paper] no quotes available, skipping tick")
        return {"status": "no_data"}

    # 2. Research reports: cached within TTL; generate via market_researcher
    #    on miss/stale and store in the shared research cache (also used by
    #    the chat assistant — one report serves both).
    reports = await _ensure_research(prices)

    # 3. Account state for the prompt
    positions_text = "\n".join(
        f"- {s}: {p.shares} shares @ {p.cost_basis} (bought {p.buy_date})"
        for s, p in account.positions.items()
    ) or "(no positions)"
    equity = account.equity(prices)

    prompt = (
        f"Account state:\n"
        f"- Cash: {account.cash:.2f}\n"
        f"- Equity: {equity:.2f}\n"
        f"- Positions:\n{positions_text}\n\n"
        f"Signals and research for this tick:\n"
        + "\n\n".join(
            f"{s}:\n{signals[s]}\nResearch: {reports[s]}"
            for s in signals
        )
        + "\n\nOutput your decisions as strict JSON."
    )

    # 3. Agent decides (structured output — TradeDecisions instance)
    try:
        result = await paper_trader.run({"input": prompt})
        parsed = result.get("output")
        decisions = list(parsed.decisions) if parsed else []
    except ValueError as e:
        # Structured-output parse failure — skip this tick rather than
        # executing anything on malformed output
        logger.error("[paper] agent output unparseable: %s", e)
        return {"status": "parse_error", "message": str(e)[:300]}

    logger.info("[paper] agent proposed %d decision(s)", len(decisions))

    # 4. Execute with rule enforcement
    executed = []
    blocked = []
    today = date.today()

    for d in decisions:
        # Every decision is untrusted LLM output — each one gets its own
        # try/except so one malformed entry can never kill the whole tick.
        try:
            # TradeDecision is a typed object (pydantic/dataclass) — attribute access
            symbol = str(d.symbol).upper().strip()
            action = str(d.action).lower()
            shares = int(d.shares or 0)
            reason = (d.reason or "").strip()[:200] or "agent decision"
        except (AttributeError, TypeError, ValueError) as e:
            blocked.append({"symbol": "?", "reason": f"malformed decision: {e}"})
            continue

        price = prices.get(symbol)

        if price is None or price <= 0:
            blocked.append({"symbol": symbol, "reason": f"invalid price {price}"})
            continue

        # Per-market trading hours: an A-share can't trade during US hours
        # even though the global tick gate saw "some market open"
        tradable, why_not = is_symbol_tradable(symbol)
        if not tradable:
            blocked.append({"symbol": symbol, "reason": why_not})
            continue

        try:
            if action == "buy":
                # Position cap: value after buy <= max_position_pct * equity
                cap_value = config.max_position_pct * equity
                current_value = (
                    account.positions[symbol].shares * price
                    if symbol in account.positions else 0.0
                )
                if shares <= 0:
                    blocked.append({"symbol": symbol, "reason": "invalid share count"})
                    continue
                affordable = int(account.cash / (price * (1 + config.fee_rate)))
                shares = min(shares, affordable)
                allowed = int((cap_value - current_value) / price)
                shares = min(shares, allowed)
                if shares <= 0:
                    blocked.append({
                        "symbol": symbol,
                        "reason": f"position cap {config.max_position_pct:.0%} or cash limit",
                    })
                    continue
                trade = account.buy(symbol, shares, price, reason, config.fee_rate)
                executed.append(trade)

            elif action == "sell":
                trade = account.sell(
                    symbol, shares, price, reason, config.fee_rate,
                    t_plus_1=config.t_plus_1, today=today,
                )
                executed.append(trade)

            else:
                continue  # hold

        except ValueError as e:
            blocked.append({"symbol": symbol, "reason": str(e)})

    # 5. Forced stop-loss (independent of the agent). A stop can only
    # execute when the symbol's own market is open — a 2 AM forced sell
    # of an A-share is not possible in reality, so it's blocked and
    # remains armed for the next session.
    for symbol, pos in list(account.positions.items()):
        price = prices.get(symbol)
        if price is None:
            continue

        tradable, why_not = is_symbol_tradable(symbol)
        if not tradable:
            continue  # market closed — the stop stays armed

        if price <= pos.cost_basis * (1 - config.stop_loss_pct):
            try:
                trade = account.sell(
                    symbol, 0, price,
                    f"forced stop-loss: price {price} below "
                    f"{pos.cost_basis * (1 - config.stop_loss_pct):.2f}",
                    config.fee_rate, t_plus_1=config.t_plus_1, today=today,
                )
                executed.append(trade)
            except ValueError as e:
                blocked.append({"symbol": symbol, "reason": f"forced stop blocked: {e}"})

    # 6. Persist + snapshot
    account.snapshot(prices)
    account.save()

    return {
        "status": "ok",
        "executed": [{"action": t.action, "symbol": t.symbol,
                      "shares": t.shares, "price": t.price,
                      "pnl": t.realized_pnl, "reason": t.reason} for t in executed],
        "blocked": blocked,
        "equity": round(equity, 2),
        "cash": round(account.cash, 2),
    }


def rebuild_trader_memory(limit: int = 6) -> None:
    """Rebuild the decision agent's memory from the persisted trade ledger.

    Called at worker startup so the agent remembers its own decisions
    across server restarts. trades.json is the source of truth; each trade
    becomes one synthesized exchange in the buffer.
    """
    account = PaperAccount.load()
    memory = paper_trader.memory
    memory.clear()
    for trade in account.trades[-limit:]:
        memory.add(
            "user",
            f"[tick {trade.timestamp}] Account state and market signals "
            f"were provided; your decision is recorded below.",
        )
        memory.add(
            "assistant",
            f"Decision: {trade.action.upper()} {trade.symbol} "
            f"{trade.shares} shares @ {trade.price}. Reason: {trade.reason}",
        )
    if len(memory) > 0:
        logger.info("[paper] trader memory rebuilt from %d trade(s)", len(memory) // 2)


async def start_paper_trader() -> None:
    """Background worker: run a tick every check_interval_min minutes.

    The interval is re-read from config after every tick (callable interval
    on to_worker) — changing check_interval_min in the UI takes effect
    without restarting the server.
    """
    async def _loop_tick() -> None:
        summary = await paper_tick()
        # Log EVERY tick — a hold decision is a result, not silence
        if summary.get("executed"):
            logger.info("[paper] tick done — %d trade(s) executed", len(summary["executed"]))
        elif summary.get("blocked"):
            logger.info(
                "[paper] tick done — no trades, %d blocked: %s",
                len(summary["blocked"]),
                "; ".join(f"{b['symbol']}: {b['reason']}" for b in summary["blocked"][:3]),
            )
        elif summary.get("status") == "skipped":
            logger.info("[paper] tick skipped: %s", summary.get("reason"))
        else:
            logger.info("[paper] tick done — no trades (agent held)")

    config = load_config()
    rebuild_trader_memory()  # survive restarts: memory ← trades.json
    logger.info("[paper] paper trader starting — interval=%smin, capital=%s",
                config.check_interval_min, config.initial_capital)
    await kg.to_worker(
        _loop_tick,
        interval=lambda: load_config().check_interval_min * 60,
    )
