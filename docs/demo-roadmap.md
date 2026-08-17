# kitegen Stock Analyst Demo — Roadmap

> ⚠️ **历史文档** — 方向已合并进 [`strategy.md`](strategy.md)，本文保留作为助手功能初稿参考。
> Goal: evolve the demo into a personal stock analyst that helps track portfolios, analyze opportunities, and execute a disciplined trading strategy.
> Disclaimer: this is an analysis and decision-support tool, not financial advice.

---

## 1. Vision

A personal stock analyst that:

- Knows your portfolio, cost basis, and targets.
- Monitors markets continuously and alerts you when conditions match your rules.
- Explains every recommendation with data and reasoning.
- Supports you through a full trade cycle: idea → analysis → decision → execution tracking → post-trade review.

Built on kitegen, so every new feature also forces the framework to become more production-ready.

---

## 2. Core Concepts

| Concept | Description |
|---|---|
| **Portfolio** | A collection of positions (symbol, shares, cost basis, target allocation). |
| **Position** | One holding in one symbol. |
| **Watchlist** | Symbols you're tracking but don't own. |
| **Rule / Strategy** | A condition + action, e.g. "if RSI < 30 and price > 200-day MA, flag as buy candidate." |
| **Trade Plan** | A proposed action with entry, stop-loss, take-profit, and position size. |
| **Alert** | A notification that a rule or price threshold has triggered. |
| **Report** | A generated summary of portfolio health, opportunities, and executed decisions. |

---

## 3. Feature Roadmap

### Phase A: Portfolio Foundation

1. **Portfolio data model** ✅
   - Store positions, cost basis, shares, target allocation. ✅
   - Track cash balance and buying power. ✅
   - Persist to a JSON file or SQLite database. ✅（JSON）

2. **Portfolio analysis tools** ✅
   - `portfolio_value()`: total value, cash, equity. ✅（`Portfolio.equity`）
   - `position_pnl(symbol)`: realized + unrealized P&L. ✅（`position_pnl/all_pnl`）
   - `portfolio_allocation()`: sector/geography/stock weights. ◐（权重 ✅，行业/地域 pending）
   - `biggest_losers()`: positions with largest unrealized losses. ✅

3. **Portfolio-aware agent** ✅
   - Agent reads the user's portfolio before answering. ✅（三步流水线 advice 阶段）
   - "Should I sell my losing NVDA position?" → agent sees cost basis, current P&L, allocation impact. ✅

### Phase B: Trading Discipline

4. **Trade plan generation** — pending → strategy S3
   - For any symbol, agent proposes entry, stop-loss, take-profit, and position size.
   - Position sizing based on risk per trade (e.g. 1% of portfolio).

5. **Stop-loss / take-profit tracking** ◐
   - User sets alerts per position. ✅（`set_stop_loss`/`set_take_profit` 工具）
   - Agent evaluates daily whether any alerts triggered. ❌ pending → strategy S1 盯盘告警

6. **Loss-recovery helper** — pending
   - Identify losing positions and suggest options: hold / average down / cut loss.
   - Show breakeven price and required recovery move.

7. **Rebalancing suggestions** — pending
   - Compare current allocation to target allocation.
   - Suggest buys/sells to rebalance.

### Phase C: Market Intelligence

8. **Watchlist with rules** — pending → strategy S8

9. **Multi-timeframe analysis** — pending
   - Agent fetches daily, weekly, monthly data.

10. **Technical indicators** ◐
    - MA20/50、RSI14、趋势信号 ✅
    - MACD、KDJ、Bollinger Bands、ATR ❌ pending → strategy S6

11. **Earnings / event calendar** — pending → strategy S5（公告/财报数据源）

### Phase D: Execution & Review

12. **Paper trading mode** — pending → strategy S4

13. **Trade journal** — pending → strategy S4

14. **Scheduled reports** — pending → strategy S1（每日早报）

15. **Exportable reports** — pending

### Phase E: UX & Distribution

16. **Web dashboard** ◐
    - Portfolio overview page. ◐（持仓芯片 ✅；完整 dashboard pending）
    - Watchlist page. ❌
    - Chat + reports side-by-side. ◐（chat + 阶段流水线 ✅）

17. **Notifications** — pending → strategy S1/S9

18. **Multi-account support** — pending

---

## 4. kitegen Framework Improvements Driven by the Demo

Each demo feature creates a framework requirement. Map:

| Demo Feature | kitegen Capability Needed |
|---|---|
| Portfolio persistence | Better `Checkpointer` interface; SQLite saver |
| Scheduled reports | Background worker runner (`kg.to_worker`) |
| Watchlist scanning | Parallel tool execution in `Agent` |
| Technical indicators | Tool result caching |
| Trade plan output | Structured output / `output_schema` on `Agent` |
| Alert rules | Conditional scheduling + stateful agents |
| Chat + dashboard | FastAPI/SSE integration helpers |
| Trade journal | Observability hooks (`on_tool_call`, `on_agent_complete`) |
| Multi-account | Namespaced state / multi-tenant checkpoints |
| Paper trading | Branching state / hypothetical checkpoints |
| Reasoning transparency | Agent introspection + citation tracking |

---

## 5. Portfolio Tracking — Detailed Design

### 5.1 Data Model

```python
class Position(BaseModel):
    symbol: str
    shares: float
    cost_basis: float          # average cost per share
    target_allocation: float   # 0.0 - 1.0 of equity
    stop_loss: float | None
    take_profit: float | None
    tags: list[str] = []        # e.g. ["tech", "speculation", "dividend"]

class Portfolio(BaseModel):
    cash: float
    positions: dict[str, Position]
    currency: str = "USD"

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(
            p.shares * prices.get(s, 0) for s, p in self.positions.items()
        )

    def pnl(self, prices: dict[str, float]) -> dict[str, dict]:
        result = {}
        for symbol, pos in self.positions.items():
            price = prices.get(symbol, 0)
            market_value = pos.shares * price
            cost = pos.shares * pos.cost_basis
            result[symbol] = {
                "market_value": market_value,
                "cost": cost,
                "unrealized_pnl": market_value - cost,
                "unrealized_pnl_pct": (market_value - cost) / cost if cost else 0,
                "weight": market_value / self.equity(prices) if self.equity(prices) else 0,
            }
        return result
```

### 5.2 Key Tools

```python
@kg.tool
def get_portfolio_summary(portfolio_id: str = "default") -> dict:
    """Return total value, cash, equity, and allocation."""
    ...

@kg.tool
def get_position_analysis(symbol: str, portfolio_id: str = "default") -> dict:
    """Return P&L, weight, and key metrics for a held position."""
    ...

@kg.tool
def get_biggest_losers(limit: int = 5, portfolio_id: str = "default") -> list[dict]:
    """Return the positions with the largest unrealized losses."""
    ...

@kg.tool
def generate_trade_plan(
    symbol: str,
    risk_pct: float = 0.01,
    portfolio_id: str = "default",
) -> dict:
    """Propose entry, stop-loss, take-profit, and position size."""
    ...
```

### 5.3 Agent Prompt

```python
analyst = kg.Agent(
    role="personal portfolio analyst",
    goal=(
        "Help the user manage their portfolio. Before recommending a trade, "
        "check the portfolio, current prices, and the user's stated risk tolerance. "
        "Always explain the reasoning and cite the numbers you used."
    ),
    tools=[
        get_portfolio_summary,
        get_position_analysis,
        get_biggest_losers,
        generate_trade_plan,
        lookup_stock,
        compute_indicator,
    ],
    llm=kg.OpenAIAdapter(model="gpt-4o"),
)
```

### 5.4 Loss-to-Profit Workflow

A dedicated workflow for underwater positions:

1. Identify positions with unrealized loss > threshold (e.g. -10%).
2. Fetch current fundamentals and technicals.
3. Agent evaluates:
   - Is the thesis still valid?
   - What is the breakeven price?
   - What average-down price would reduce breakeven to current level?
   - What stop-loss would cap further loss?
4. Present options:
   - Hold to target
   - Average down with new stop
   - Cut loss and redeploy capital
   - Sell covered call / cash-secured put (if options enabled)
5. If user chooses action, update portfolio and log trade journal entry.

---

## 6. Suggested Implementation Order

1. Build `Portfolio` and `Position` models + JSON persistence.
2. Add portfolio tools to the agent.
3. Make agent read portfolio context automatically before answering.
4. Build the "biggest losers" analysis workflow.
5. Add trade plan generation with position sizing.
6. Add stop-loss / take-profit alert rules.
7. Build a simple portfolio dashboard in the React frontend.
8. Add scheduled daily scan using background worker.

---

## 7. Non-Goals

- Real-time order execution (brokerage integration is out of scope).
- Tax optimization across jurisdictions.
- Options/futures advanced strategies (can be added later).
- Real-time L2 data.

---

## 8. Open Questions

1. Should the agent proactively message the user, or only respond to questions?
2. Should trades be executed in "paper mode" first?
3. How aggressive should loss-recovery suggestions be by default?
4. Should the system support multiple portfolios (e.g. retirement vs speculation)?
5. What is the primary data source priority once YahooQuery + Tencent are in place?
