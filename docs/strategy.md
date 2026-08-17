# kitegen Strategy

> This is the single authoritative strategy document. It merges and supersedes the direction sections of `design-plan.md` and `demo-roadmap.md` (both kept as historical reference).
> Last updated: 2026-08-17

---

## 1. Goals

Two mutually reinforcing goals:

1. **kitegen**: continuously evolve into a production-grade agent framework with a distinctive identity.
2. **Stock assistant**: build an all-in-one trading partner for personal use (analysis + discipline + alerts, no real order execution).

Core idea: **the stock assistant is the framework's proving ground — every framework capability is driven by a real need, and every assistant feature validates the framework's positioning.**

---

## 2. Positioning

> **kitegen = a Python framework for building AI workflows with human-in-the-loop. Agent, Task, Crew, Graph compose freely; deploy with one line as script / API / Worker / Stream.**

Differentiation pillars:

| Pillar | Now | Target |
|--------|-----|--------|
| **Everything is Executable** | Agent/Graph/function share one protocol ✅ | Task/Crew on the same protocol; Agent can delegate to sub-agents |
| **Human-in-the-loop native** | Basic `interrupt()` + `resume()` | Approval tiers, timeout+escalation, edit-in-loop, audit log |
| **Write once, run anywhere** | — | `to_fastapi() / to_worker() / to_cli() / to_sse()` |
| **China ecosystem first** | Tencent quotes, A-share tooling, DeepSeek adapters ✅ | Chinese docs, built-in A-share data sources |
| **Streaming observability** | Two parallel streaming paths (tech debt) | One unified event system: token/tool/node events from any nesting depth flow in a single stream |

---

## 3. kitegen Framework Directions

### 3.1 Done ✅

- Executable protocol + Context + RetryPolicy + event system (`core.py`)
- `@tool` decorator with type-hint schema inference (`tool.py`)
- LLM adapters: OpenAI / Anthropic / LiteLLM (`llm.py`) — env-driven models (`LLM_MODEL`), optional usage tracker
- Agent class: ReAct tool loop, persona rendering, max_iterations (`agent.py`)
- **F7 Structured output**: `Agent(output_schema=...)` — final answer parsed into a Pydantic model or dataclass; markdown fences stripped; parse failures raise loudly
- **F6 Memory protocol**: `Memory` protocol + `BufferMemory` (`memory.py`) — previous exchanges injected between system prompt and user message, recorded after successful runs
- Graph: streaming with cancel, interrupt/resume, conditional routing, checkpoint **merge semantics** (`graph.py`)
- Resilience: CircuitBreaker, TokenTracker (serializable), Usage cost tracking
- Checkpointer: MemorySaver / PostgresSaver
- **F4 (partial)**: `to_worker()` scheduling (`deploy.py`) — error-tolerant interval loop with stop_event
- **72 tests, all green**

### 3.2 To Do (by priority)

| # | Work | Why |
|---|------|-----|
| F1 | **Unified streaming event system** | Biggest tech debt. Graph ContextVar queue and `context.stream()` coexist; agent events are lost when nested in a graph; tokens are "replayed after the fact". After unification, events from any depth flow in one stream |
| F2 | **Adapter-level token streaming** | LLM adapters get `chat_stream()`; Agent loop emits `TokenEvent` — true streaming |
| F3 | **Publish PyPI v0.2 + docs site** | Framework is 700+ lines, tests green. No publish = no user feedback loop. MkDocs site + LangGraph/CrewAI migration guide |
| F4 | **Deployment layer (remaining)**: `to_fastapi()` | `to_worker` done. The demo hand-rolls FastAPI+SSE+CORS — extract it so any Executable becomes an API in one line |
| F5 | **HITL upgrades** | Approval tiers (different thresholds route to different people), timeout+escalation, edit intermediate results, audit log. "Suggest sell, confirm with human" is a finance must-have |
| F8 | **Minimal observability** | Structured trace: per-node latency/token cost/full message record, JSONL output. **No OpenTelemetry** (too heavy); Langfuse as a plugin later |
| F9 | **Checkpoint versioning** | `save(state, thread_id, step=N)` for replay/rollback |
| F10 | Task / Crew (lightweight) | Task (template + executable + output_key); Crew as Graph syntactic sugar, no new runtime |
| F11 | **Plugin interface** | Let real needs define it. Building a plugin market before users = buying planes before an airport |

---

## 4. Stock Assistant Directions

### 4.1 Done ✅

- Portfolio data model + JSON persistence (`portfolio.py`)
- Position tools: get/list/record/remove/set_stop_loss/set_take_profit (`agents.py`)
- 3-agent pipeline: research → strategy → personalized advice (kitegen Graph)
- **S1 Market monitoring**: stop-loss/take-profit/move checks + daily briefing via `kg.to_worker`, webhook notifications + frontend alerts panel
- **S6 Indicators (partial)**: `compute_signal` transparent vote-based regime (trend/momentum/MACD) + full indicator engine (MACD/ATR/Bollinger) + ATR key levels
- **Research report cache**: research stage cached per symbol + TTL, separated from real-time advice (~26% token savings on same-symbol follow-ups)
- **Bidirectional trade plans**: every recommendation must have exit / add-on / do-nothing scenarios + `calculate_position_size` risk sizing
- Tencent quotes (A-share/HK/US) + smartbox dynamic name resolution — **China-first ordering** (Tencent before yahooquery for CN/HK)
- Fundamentals + technicals (MA20/50, RSI14, trend regime)
- **S4 Paper trading (core)**: virtual account with T+1/position caps/forced stops, decision agent with structured output (`TradeDecisions`) + memory, trading-hours gates (global + **per-market**), research-report integration (shared cache with the chat assistant), full persistence, restart recovery, trade notifications
- React frontend: portfolio panel (CRUD form + live price polling), stage stepper, markdown rendering, stop button, alerts panel, expandable briefing, paper trading panel (equity curve SVG + config form + run/reset), browser notifications, confirmation dialogs
- Session history + current-question separation (checkpoint merge semantics)
- **Usage tracking**: persistent `TokenTracker` (survives restarts) + live footer display
- **Code review pass**: 2 Critical + 8 Important findings fixed (forced-stop dead code, tick concurrency lock, atomic writes, per-decision isolation, config validation, etc.) + engine-level tests added

### 4.2 To Do (by priority)

| # | Work | Value |
|---|------|-------|
| S2 | **Personal rules engine**: encode discipline ("cut loss at -8%", "new position ≤ 10% capital") checked before every recommendation | From chat tool to personal trading system |
| S3 | **Trade Plan tool**: structured entry/stop/target/position size/R:R output | Actionable advice |
| S4 | **Paper trading evaluation**: win rate, profit factor, max drawdown, benchmark vs buy-and-hold | The core engine is done; the metrics layer closes the loop (needs accumulated data) |
| S5 | **A-share data sources**: announcements/filings (CNINFO/Eastmoney), capital flow, Dragon-Tiger list, news sentiment | Analysis depth |
| S6 | **Indicator expansion**: KDJ, volume ratio (MACD/BOLL/ATR done) | Each is a pure-function `@tool` |
| S7 | **Portfolio risk**: sector concentration, correlation, weight deviation alerts | Risk awareness |
| S8 | **Watchlist + conditional scans**: conditions per watched symbol, periodic matching | Opportunity discovery |
| S9 | **Multi-channel reach**: Telegram bot, email reports, voice | All-day coverage |
| S10 | **Chart visualization**: K-line/RSI/equity curve images | See it at a glance |

Data source principle: **China first**. Tencent quotes + Eastmoney/CNINFO primary; no US-market tooling (portfolio is A-share + HK + US mixed; Tencent covers all).

### 4.3 S4 Paper Trading — Detailed Spec (new requirement)

> Goal: manually configure a simulated trading agent that watches the market and trades automatically, to **test the agent's trading ability** (validate in simulation before going live).

#### Configuration (all manually adjustable)

```python
paper_config = {
    "initial_capital": 500_000,     # starting capital
    "check_interval_min": 30,       # how often to check the market
    "max_position_pct": 0.20,       # max position size (% of equity — UI shows percent, backend stores fraction)
    "stop_loss_pct": 0.08,          # stop-loss discipline
    "t_plus_1": True,               # T+1: shares bought today sellable tomorrow at the earliest
    "fee_rate": 0.0003,             # commission (optional, default 0.03%)
    "enabled_symbols": None,        # None = all held + watchlisted, or an explicit list
    "trading_hours_only": True,     # skip ticks outside trading hours
}
```

#### Trading engine rules

1. **Watch loop**: reuses `kg.to_worker()`, interval = `check_interval_min`. Each tick: fetch quotes → `compute_signal` → research reports (cache-or-generate) → decision agent → rule engine → simulate execution.
2. **Decision source**: a dedicated decision agent with structured output (`TradeDecisions` via F7) + memory of prior decisions (F6, rebuilt from trades.json across restarts) + hard-rule interception (position cap, T+1, forced stop-loss, cash checks).
3. **T+1 constraint**: engine validates `sellable_date = buy_date + 1`; refuses early sells. Applies to A-shares only (`.SS`/`.SZ`); US/HK have no such restriction. `shares=0` on sell means "entire position".
4. **Trading hours**: two-layer gate — global (any market open → tick runs) + **per-symbol** (`is_symbol_tradable`: A-share only during CN sessions, HK during HK, US during US). Forced stops also respect per-market hours (a 2 AM A-share stop stays armed until the next session).
5. **Simulated fill price**: current quote at check time (no order-book simulation).
6. **Isolated from real holdings**: separate virtual account + separate data files; never touches the `default` portfolio or live alerts.
7. **Concurrency**: `paper_tick` serialized by an `asyncio.Lock` (manual trigger + background worker).

#### Persistence (`demo/data/paper/`)

| File | Content |
|------|---------|
| `paper/account.json` | Virtual account: cash, positions, equity-curve snapshots (atomic writes) |
| `paper/config.json` | The config above (edit via UI; interval changes take effect on restart) |
| `paper/trades.json` | Every trade: time, direction, price, quantity, **decision rationale** (the agent's recommendation text) |

#### Capability evaluation (does the agent actually trade well?) — NOT YET BUILT

| Metric | Formula |
|--------|---------|
| Total return | ending equity / initial capital − 1 |
| Win rate | winning closed trades / total closed trades |
| Profit factor | average win / average loss |
| Max drawdown | peak-to-trough of the equity curve |
| Benchmark | vs buy-and-hold (same capital, same initial holdings) vs CSI 300 |

Output: trade ledger + evaluation report (weekly/monthly), plus a Paper Trading page in the frontend showing the equity curve and trade history.

#### Implementation status (2026-08-17)

**Built**: account model + T+1 + weighted cost basis, decision agent (structured output + memory + restart rebuild), rule enforcement (caps/T+1/forced stops/per-market hours), persistence with atomic writes, tick mutex, research-report integration via the shared cache, frontend panel (equity curve SVG, config form with percent semantics, Run Tick/Reset with confirmation dialog), browser trade notifications, config validation.

**Known simplifications** (documented deviations): A-share board lots (100-share rounds) not modeled; T+1 uses the oldest buy date (conservative over-blocking on averaging up); no holiday calendar (Chinese holidays / US DST); `BufferMemory` is per-process (rebuilt from trades.json at startup).

#### Why this shape

- Reuses all existing infrastructure: pipeline signals/research cache, monitor worker pattern, portfolio model, usage stats
- "Manual config + automatic execution" is the second piece of the F4 deployment layer (`to_worker` is already in place)
- Everything lands on disk → replayable, auditable, comparable across configs (change parameters and rerun)

---

## 5. Unified Execution Order

Each step makes framework capability and assistant needs drive each other. Status as of 2026-08-17:

| Step | Work | Status |
|------|------|--------|
| 1 | F1 unified streaming + F2 true token streaming | ⏳ next — the root of all UI streaming compromises |
| 2 | F3 publish PyPI + docs | ⏳ cheap, no dependencies |
| 3 | F4 deployment layer | ◐ `to_worker` ✅; `to_fastapi` extraction pending |
| 4 | S1 market monitoring | ✅ done |
| 5 | F5 HITL upgrades | ⏳ |
| 6 | S2 rules engine + S3 Trade Plan | ◐ F7 structured output ✅ (drives S4); S2/S3 pending |
| 7 | S4 paper trading + S5 data sources + S6 indicators | ◐ S4 core ✅ + F6 memory ✅; S4 evaluation metrics, S5, S6 (KDJ/volume ratio) pending |
| 8 | F10 Task/Crew + F11 plugins | ⏳ |

---

## 6. Non-Goals

- Real order execution / brokerage integration (decision support, not execution)
- Direct OpenTelemetry (minimal trace first; plugin later)
- US-market tooling (Alpha Vantage/Finnhub/websocket quotes — China first)
- Premature plugin marketplace
- Built-in vector DB / RAG framework (RAG is a tool pattern, not a framework feature)

---

## 7. Document Index

| Document | Status |
|----------|--------|
| `docs/strategy.md` | **Authoritative** — positioning + dual-track directions + execution order |
| `docs/design-plan.md` | Historical — six-phase framework draft, merged here |
| `docs/demo-roadmap.md` | Historical — five-phase assistant draft, merged here |
| `docs/superpowers/specs/*` | Historical — implemented stream/agent design specs |
