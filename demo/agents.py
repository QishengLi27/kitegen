"""Agent definitions for the kitegen stock analyst demo.

Three agents wired as a pipeline graph:
    research (market_researcher) -> strategy (trading_strategist) -> advice (portfolio_analyst)

Every strategy prompt requires BIDIRECTIONAL trade plans:
    - exit scenario (stop-loss / take-profit)
    - add-on scenario (trigger conditions + position sizing)
    - the conditions under which neither applies
"""

from __future__ import annotations

import os

import kitegen as kg
from demo.portfolio import Position, Portfolio, load_portfolio
from demo.tools import (
    calculate_position_size,
    compute_signal,
    get_fundamentals,
    get_technical_summary,
    lookup_stock,
    resolve_symbol,
)


# ── Risk-mode prompt tailoring ───────────────────────────────────────────────

RISK_MODE_INSTRUCTIONS = {
    "conservative": (
        "You are in CONSERVATIVE mode. Prioritize capital preservation above all. "
        "Only recommend trades with a clear risk/reward edge. Favor tight stop-losses, "
        "smaller add-ons, and doing nothing when the setup is marginal. "
        "If a position is underwater, prefer reducing risk over aggressive averaging down."
    ),
    "normal": (
        "You are in NORMAL mode. Balance risk and return. Use sensible position sizing, "
        "clear stop-losses, and add-on scenarios only when the technical setup justifies it."
    ),
    "aggressive": (
        "You are in AGGRESSIVE mode. The user accepts larger risk and wider drawdowns "
        "in pursuit of higher returns or faster recovery from losses. "
        "You may recommend larger positions, momentum entries, wider stops, and averaging "
        "down on conviction setups. Be decisive and explicit about the risk being taken."
    ),
}


def _risk_personality(risk_mode: str) -> tuple[str, str]:
    """Return (goal_suffix, personality) for a risk mode."""
    instructions = RISK_MODE_INSTRUCTIONS.get(risk_mode, RISK_MODE_INSTRUCTIONS["normal"])
    if risk_mode == "conservative":
        personality = (
            "Cautious and protective. You think in terms of 'what can go wrong first' "
            "and only act when the odds are clearly favorable."
        )
    elif risk_mode == "aggressive":
        personality = (
            "Bold and opportunity-focused. You accept volatility and prioritize setups "
            "with asymmetric upside, while still labeling the downside clearly."
        )
    else:
        personality = (
            "Data-driven and disciplined. You care about risk management, cost basis, "
            "and realistic price targets. You never give vague advice."
        )
    return instructions, personality


def _normalize_symbol(symbol: str) -> str:
    """Normalize a symbol to portfolio format (000725/BOE -> 000725.SZ)."""
    s = str(symbol).upper().strip()
    portfolio = load_portfolio("default")
    if s in portfolio.positions:
        return s
    return resolve_symbol(s) or s


def _get_position_summary(symbol: str, portfolio_id: str = "default") -> str:
    """Return a formatted summary of the user's position in a symbol."""
    sym = _normalize_symbol(symbol)
    portfolio = load_portfolio(portfolio_id)
    position = portfolio.positions.get(sym)
    if not position:
        return f"You do not currently hold a position in {symbol}."

    return (
        f"Your position in {sym}:\n"
        f"Shares: {position.shares}\n"
        f"Cost basis: {position.cost_basis}\n"
        f"Stop loss: {position.stop_loss}\n"
        f"Take profit: {position.take_profit}\n"
        f"Target allocation: {position.target_allocation * 100}%\n"
        f"Tags: {position.tags}"
    )


@kg.tool
def get_my_position(symbol: str) -> str:
    """Get the user's current position (shares, cost basis, targets) for a symbol.

    Use whenever the user asks about a stock they own.
    """
    return _get_position_summary(_normalize_symbol(symbol))


@kg.tool
def list_my_positions() -> str:
    """List all positions in the user's portfolio."""
    portfolio = load_portfolio("default")
    if not portfolio.positions:
        return "Your portfolio is currently empty."
    lines = ["Your portfolio:"]
    for symbol, pos in portfolio.positions.items():
        lines.append(
            f"- {symbol}: {pos.shares} shares @ {pos.cost_basis} cost basis"
        )
    return "\n".join(lines)


@kg.tool
def record_position(symbol: str, shares: float, cost_basis: float) -> str:
    """Record or update the user's stock position.

    Call this IMMEDIATELY when the user says they bought, hold, or own shares.
    Do NOT wait for further instructions.

    English examples:
        "I bought 100 AAPL at $180" -> record_position("AAPL", 100, 180)
        "I hold 50 shares of 600519.SS at 1600 cost" -> record_position("600519.SS", 50, 1600)

    Chinese examples (common phrases to recognize):
        "我买了2500股000725.SZ，成本6.043" -> record_position("000725.SZ", 2500, 6.043)
        "我持有100股AAPL，成本180美元" -> record_position("AAPL", 100, 180)
        "买入腾讯0700.HK 200股，成本375" -> record_position("0700.HK", 200, 375)
    """
    from demo.portfolio import Position, load_portfolio, save_portfolio

    symbol = _normalize_symbol(symbol)
    if shares <= 0 or cost_basis <= 0:
        return "Shares and cost basis must be positive numbers."

    portfolio = load_portfolio("default")
    portfolio.positions[symbol] = Position(
        symbol=symbol,
        shares=float(shares),
        cost_basis=float(cost_basis),
    )
    save_portfolio(portfolio, "default")
    return f"Recorded: {shares} shares of {symbol} at cost basis {cost_basis}."


@kg.tool
def remove_position(symbol: str) -> str:
    """Remove a symbol from the user's portfolio. Use when they sold out.

    Examples:
        "I sold all my AAPL" -> remove_position("AAPL")
        "清空NVDA" -> remove_position("NVDA")
    """
    from demo.portfolio import load_portfolio, save_portfolio

    symbol = _normalize_symbol(symbol)
    portfolio = load_portfolio("default")
    if symbol in portfolio.positions:
        del portfolio.positions[symbol]
        save_portfolio(portfolio, "default")
        return f"Removed {symbol} from your portfolio."
    return f"You did not hold {symbol}."


@kg.tool
def set_stop_loss(symbol: str, price: float) -> str:
    """Set a stop-loss price for a position.

    Call when the user asks to set a stop (止损 / stop loss / 止损价).

    Examples:
        "Set AAPL stop at 180" -> set_stop_loss("AAPL", 180)
        "给茅台设个1250的止损" -> set_stop_loss("600519.SS", 1250)
    """
    from demo.portfolio import load_portfolio, save_portfolio

    symbol = _normalize_symbol(symbol)
    portfolio = load_portfolio("default")
    if symbol not in portfolio.positions:
        return f"You do not hold {symbol}. Record the position first with record_position."

    if price <= 0:
        return "Stop-loss price must be positive."

    portfolio.positions[symbol].stop_loss = float(price)
    save_portfolio(portfolio, "default")
    return f"Stop loss for {symbol} set at {price}."


@kg.tool
def set_take_profit(symbol: str, price: float) -> str:
    """Set a take-profit target price for a position.

    Call when the user asks to set a target (止盈 / take profit / 目标价).

    Examples:
        "NVDA target 250" -> set_take_profit("NVDA", 250)
        "腾讯止盈500" -> set_take_profit("0700.HK", 500)
    """
    from demo.portfolio import load_portfolio, save_portfolio

    symbol = _normalize_symbol(symbol)
    portfolio = load_portfolio("default")
    if symbol not in portfolio.positions:
        return f"You do not hold {symbol}. Record the position first with record_position."

    if price <= 0:
        return "Take-profit price must be positive."

    portfolio.positions[symbol].take_profit = float(price)
    save_portfolio(portfolio, "default")
    return f"Take profit for {symbol} set at {price}."


# ── Agents ───────────────────────────────────────────────────────────────────


def _get_llm() -> kg.OpenAIAdapter:
    """Create the LLM adapter from env vars.

    Resolution order (all optional):
        LLM_API_KEY / OPENAI_API_KEY     — API key
        LLM_API_BASE / OPENAI_BASE_URL   — API base URL
        LLM_MODEL / OPENAI_MODEL         — model name (default: gpt-4o)

    Example .env for DeepSeek:
        LLM_API_KEY=sk-...
        LLM_API_BASE=https://api.deepseek.com/v1
        LLM_MODEL=deepseek-v4-flash

    All agents share the module-level TokenTracker (llm_tracker) so token
    usage and cost aggregate across the whole pipeline.
    """
    return kg.OpenAIAdapter(tracker=llm_tracker)


# Shared across all agents — every LLM call records usage here.
# Persisted to data/usage.json so usage survives server restarts.
llm_tracker = kg.TokenTracker()


def _load_usage_history() -> None:
    import json as _json
    from pathlib import Path as _Path

    path = _Path(__file__).parent / "data" / "usage.json"
    if path.exists():
        try:
            llm_tracker.load_records(_json.loads(path.read_text(encoding="utf-8")))
        except (OSError, _json.JSONDecodeError):
            pass


_load_usage_history()


# Shared mandatory structure for every piece of trading advice
BIDIRECTIONAL_PLAN = (
    "EVERY piece of trading advice MUST have this exact structure:\n"
    "1. EXIT SCENARIO: stop-loss level and take-profit target (or why none).\n"
    "2. ADD-ON SCENARIO: the specific trigger conditions to add to the position "
    "(e.g. price reclaims a moving average, RSI oversold rebound, volume breakout), "
    "plus a suggested add-on size computed with calculate_position_size.\n"
    "3. DO-NOTHING ZONE: the conditions under which the user should neither buy nor sell.\n"
    "Never give exit advice without the corresponding add-on scenario."
)


def make_agents(risk_mode: str = "normal") -> tuple[kg.Agent, kg.Agent, kg.Agent]:
    """Build the three pipeline agents for a given risk mode.

    The returned agents have mode-specific goals and personalities so that
    conservative/normal/aggressive requests produce appropriately different
    advice. Tools pick up the same mode via the context-var set by the caller.
    """
    risk_instruction, risk_personality = _risk_personality(risk_mode)

    analyst = kg.Agent(
        role="personal portfolio analyst",
        goal=(
            "Help the user make better trading decisions for stocks they own. "
            "When asked about a position, first fetch the user's holding details, "
            "then fetch price data, fundamentals, and technicals. "
            "Give specific, actionable suggestions for the coming days "
            "and the coming weeks, and explain your reasoning with numbers. "
            "If the user mentions a new trade or holding, use record_position to save it.\n\n"
            "IMPORTANT: when the user says they bought/hold/added shares (including "
            "Chinese phrases like 我买了... 我持有... 买入...), call record_position first, "
            "then acknowledge the saved position before analyzing.\n\n"
            "RISK MANAGEMENT: suggest concrete stop-loss and take-profit levels. "
            "If the user asks to set them (止损/止盈/stop loss/take profit), "
            "call set_stop_loss or set_take_profit.\n\n"
            f"{risk_instruction}"
        ),
        personality=risk_personality,
        system_prompt=None,
        tools=[
            get_my_position,
            list_my_positions,
            record_position,
            remove_position,
            set_stop_loss,
            set_take_profit,
            calculate_position_size,
            compute_signal,
            lookup_stock,
            get_fundamentals,
            get_technical_summary,
        ],
        llm=_get_llm(),
        max_iterations=6,
    )

    researcher = kg.Agent(
        role="stock researcher",
        goal=(
            "Understand a company from price, fundamentals, and technicals. "
            "Identify the trend regime (bull/bear/range) and the key levels "
            "that would confirm or invalidate that regime. "
            "Always call compute_signal for a composite technical reading and "
            "cite its signal, confidence, and key levels.\n\n"
            f"{risk_instruction}"
        ),
        personality=f"{risk_personality} Cite numbers.",
        tools=[lookup_stock, get_fundamentals, get_technical_summary, compute_signal],
        llm=_get_llm(),
        max_iterations=4,
    )

    strategist = kg.Agent(
        role="trading strategist",
        goal=(
            "Turn research and portfolio context into concrete trading suggestions "
            "for the next few days and the next few weeks.\n\n"
            "Always call compute_signal before giving short-term advice. "
            "Base your stop-loss and take-profit levels on the key_levels it returns "
            "(or justify a deviation with numbers).\n\n"
            f"{risk_instruction}\n\n"
            + BIDIRECTIONAL_PLAN
        ),
        personality=f"{risk_personality} Always include exit AND add-on scenarios.",
        tools=[
            get_technical_summary,
            compute_signal,
            lookup_stock,
            calculate_position_size,
        ],
        llm=_get_llm(),
        max_iterations=3,
    )

    return analyst, researcher, strategist


# Module-level normal-mode agents for backward compatibility.
portfolio_analyst, market_researcher, trading_strategist = make_agents("normal")


# ── Multi-agent pipeline ────────────────────────────────────────────────────
#
# research -> strategy -> advice, wired as a kitegen Graph. Each stage is an
# adapter node that runs its agent and passes the output to the next stage.


def _stage_prompt(state: dict, extra: str) -> str:
    """Build a stage prompt: history as context, current question explicit."""
    question = str(state.get("user_message", "")).strip()
    history = str(state.get("history", "")).strip()
    parts = []
    if history:
        parts.append(history)
        parts.append(
            "The above is previous conversation for context only. "
            "Answer ONLY the current question below."
        )
    parts.append(f"Current question: {question}")
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


async def _research_node(state: dict, researcher: kg.Agent) -> dict:
    """Stage 1: market_researcher studies the company.

    Research reports are cached per symbol (TTL hours — see demo.cache).
    The cache holds the research TEXT only; later stages still fetch fresh
    prices/indicators, so cached research never means stale advice.
    """
    from demo.cache import cache_research, get_cached_research

    symbol = state.get("symbol")
    force = bool(state.get("force_research"))

    if symbol and not force:
        cached = get_cached_research(symbol)
        if cached:
            state["research"] = cached["report"]
            state["research_cached"] = True
            await kg.stream_event({
                "type": "stage",
                "stage": "research",
                "content": (
                    f"{cached['report']}\n\n"
                    f"(cached research report, generated {cached['generated_at']})"
                ),
            })
            return state

    result = await researcher.execute({"input": _stage_prompt(state, "")})
    state["research"] = result.get("output", "")
    state["research_cached"] = False

    if symbol:
        try:
            cache_research(symbol, state["research"])
        except OSError:
            pass  # cache is best-effort

    await kg.stream_event(
        {"type": "stage", "stage": "research", "content": state["research"]}
    )
    return state


async def _strategy_node(state: dict, strategist: kg.Agent) -> dict:
    """Stage 2: trading_strategist turns research into a strategy."""
    research = state.get("research", "")
    prompt = _stage_prompt(
        state,
        f"Research findings:\n{research}\n\n"
        f"Based on this research, give a concrete trading strategy "
        f"for the next few days and the next few weeks. "
        f"Follow the mandatory EXIT / ADD-ON / DO-NOTHING structure.",
    )
    result = await strategist.execute({"input": prompt})
    state["strategy"] = result.get("output", "")
    await kg.stream_event(
        {"type": "stage", "stage": "strategy", "content": state["strategy"]}
    )
    return state


async def _advice_node(state: dict, analyst: kg.Agent) -> dict:
    """Stage 3: portfolio_analyst gives personalized advice."""
    strategy = state.get("strategy", "")
    prompt = _stage_prompt(
        state,
        f"Strategy analysis:\n{strategy}\n\n"
        f"Check the user's positions, combine with the strategy, and give "
        f"personalized, actionable advice. Include the exit scenario, the "
        f"add-on scenario (with sizing), and the do-nothing zone. "
        f"Respond in the same language as the user's question.",
    )
    result = await analyst.execute({"input": prompt})
    state["output"] = result.get("output", "")
    return state


def build_pipeline(risk_mode: str = "normal") -> tuple[kg.Graph, kg.MemorySaver]:
    """Compile the research → strategy → advice graph for a risk mode.

    Returns the compiled pipeline and the checkpointer so callers can load
    final state by thread_id.

    The agents' goals/personalities embed the mode, but the tool thresholds
    (compute_signal, position sizing) read the risk-mode context var — the
    caller must call demo.tools.set_risk_mode() in its own task so tools
    use the matching profile (demo.backend does this per request, the paper
    engine per tick).
    """
    analyst, researcher, strategist = make_agents(risk_mode)

    # Async closures — plain lambdas would return coroutines, which the
    # graph (sync-wrapper path) cannot handle.
    async def _research_fn(state: dict) -> dict:
        return await _research_node(state, researcher)

    async def _strategy_fn(state: dict) -> dict:
        return await _strategy_node(state, strategist)

    async def _advice_fn(state: dict) -> dict:
        return await _advice_node(state, analyst)

    graph = kg.Graph()
    graph.add_node("research", _research_fn)
    graph.add_node("strategy", _strategy_fn)
    graph.add_node("advice", _advice_fn)
    graph.add_edge("research", "strategy")
    graph.add_edge("strategy", "advice")
    graph.set_entry_point("research")

    saver = kg.MemorySaver()
    return graph.compile(checkpointer=saver), saver


# One pipeline per risk mode, selected per request by /chat. Building all
# three up front keeps mode switching cheap while each keeps mode-specific
# agent goals/personalities.
PIPELINES: dict[str, tuple[kg.Graph, kg.MemorySaver]] = {
    mode: build_pipeline(mode)
    for mode in ("conservative", "normal", "aggressive")
}


def get_pipeline(risk_mode: str = "normal") -> tuple[kg.Graph, kg.MemorySaver]:
    """Return the compiled pipeline (+ saver) for a risk mode.

    Unknown modes fall back to normal.
    """
    mode = str(risk_mode).lower().strip()
    return PIPELINES.get(mode, PIPELINES["normal"])


# Module-level normal-mode pipeline for backward compatibility.
pipeline, pipeline_saver = get_pipeline("normal")
