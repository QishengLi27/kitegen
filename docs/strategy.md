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
- Graph: streaming with cancel, interrupt/resume, conditional routing, checkpoint **merge semantics** (`graph.py`)
- Resilience: CircuitBreaker, TokenTracker (serializable), Usage cost tracking
- Checkpointer: MemorySaver / PostgresSaver
- `to_worker()` scheduling (`deploy.py`)
- **45 tests, all green**

### 3.2 To Do (by priority)

| # | Work | Why |
|---|------|-----|
| F1 | **Unified streaming event system** | Biggest tech debt. Graph ContextVar queue and `context.stream()` coexist; agent events are lost when nested in a graph; tokens are "replayed after the fact". After unification, events from any depth flow in one stream |
| F2 | **Adapter-level token streaming** | LLM adapters get `chat_stream()`; Agent loop emits `TokenEvent` — true streaming |
| F3 | **Publish PyPI v0.2 + docs site** | Framework is 600+ lines, tests green. No publish = no user feedback loop. MkDocs site + LangGraph/CrewAI migration guide |
| F4 | **Deployment layer: `to_fastapi()/to_worker()/to_cli()`** | Killer feature. LangGraph/CrewAI stop at "library". The demo's hand-rolled FastAPI+SSE is the blueprint |
| F5 | **HITL upgrades** | Approval tiers (different thresholds route to different people), timeout+escalation, edit intermediate results, audit log. "Suggest sell, confirm with human" is a finance must-have |
| F6 | **Memory protocol** | `Memory(Protocol): add()/get()` + built-in `BufferMemory`. Same pluggable pattern as `LLMAdapter` |
| F7 | **Structured output** | Agent supports `output_schema`, producing verifiable results like `StockAnalysis(rating, rationale, days_plan, weeks_plan)` |
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
- Tencent quotes (A-share/HK/US) + smartbox dynamic name resolution (`tools.py`)
- Fundamentals + technicals (MA20/50, RSI14, trend regime)
- React frontend: portfolio panel (CRUD form + live price polling), stage stepper, markdown rendering, stop button, alerts panel, expandable briefing
- Session history + current-question separation (checkpoint merge semantics)
- **Usage tracking**: persistent `TokenTracker` (survives restarts) + live footer display

### 4.2 To Do (by priority)

| # | Work | Value |
|---|------|-------|
| S1 | **Market monitoring**: stop/target triggers, daily moves, 8:50 morning briefing | From "answer when asked" to "reach out proactively" — killer feature |
| S2 | **Personal rules engine**: encode discipline ("cut loss at -8%", "new position ≤ 10% capital") checked before every recommendation | From chat tool to personal trading system |
| S3 | **Trade Plan tool**: structured entry/stop/target/position size/R:R output | Actionable advice |
| S4 | **Paper trading + trade journal**: simulated trades, win rate, profit factor, drawdown | Validate strategy before real money (see §4.3) |
| S5 | **A-share data sources**: announcements/filings (CNINFO/Eastmoney), capital flow, Dragon-Tiger list, news sentiment | Analysis depth |
| S6 | **Indicator expansion**: MACD, KDJ, BOLL, ATR, volume ratio | Each is a pure-function `@tool` |
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
    "max_position_pct": 0.20,       # max position size (% of equity)
    "stop_loss_pct": 0.08,          # stop-loss discipline (can be overridden by signal key levels)
    "t_plus_1": True,               # T+1: shares bought today sellable tomorrow at the earliest
    "fee_rate": 0.0003,             # commission (optional, default 0.03%)
    "enabled_symbols": None,        # None = all held + watchlisted, or an explicit list
}
```

#### Trading engine rules

1. **Watch loop**: reuses `kg.to_worker()`, interval = `check_interval_min`. Each tick: fetch quotes → `compute_signal` → rule engine decides → simulate execution.
2. **Decision source**: `trading_strategist` recommendations (reuse the pipeline's strategy stage) + hard-rule interception (position cap, T+1, forced stop-loss).
3. **T+1 constraint**: engine validates `sellable_date = buy_date + 1`; refuses early sells. Applies to A-shares only (`.SS`/`.SZ`); US/HK have no such restriction.
4. **Simulated fill price**: current quote at check time (no order-book simulation).
5. **Isolated from real holdings**: separate virtual account + separate data files; never touches the `default` portfolio or live alerts.

#### Persistence (`demo/data/paper/`)

| File | Content |
|------|---------|
| `paper/account.json` | Virtual account: cash, positions, equity-curve snapshots |
| `paper/config.json` | The config above (edit + restart to apply) |
| `paper/trades.json` | Every trade: time, direction, price, quantity, **decision rationale** (the agent's recommendation text), signal value |

#### Capability evaluation (does the agent actually trade well?)

| Metric | Formula |
|--------|---------|
| Total return | ending equity / initial capital − 1 |
| Win rate | winning closed trades / total closed trades |
| Profit factor | average win / average loss |
| Max drawdown | peak-to-trough of the equity curve |
| Benchmark | vs buy-and-hold (same capital, same initial holdings) vs CSI 300 |

Output: trade ledger + evaluation report (weekly/monthly), plus a Paper Trading page in the frontend showing the equity curve and trade history.

#### Why this shape

- Reuses all existing infrastructure: pipeline signals/strategy, monitor worker pattern, portfolio model, usage stats
- "Manual config + automatic execution" is the second piece of the F4 deployment layer (`to_worker` is already in place)
- Everything lands on disk → replayable, auditable, comparable across configs (change parameters and rerun)

---

## 5. Unified Execution Order

Each step makes framework capability and assistant needs drive each other:

| Step | Work | Framework output | Assistant output |
|------|------|------------------|------------------|
| 1 | F1 unified streaming + F2 true token streaming | Consistent event system | Real token streaming UI; nested agent events visible |
| 2 | F3 publish PyPI + docs | Feedback loop starts | — |
| 3 | F4 deployment layer | `to_fastapi/to_worker` | Worker base for S1 monitoring |
| 4 | S1 market monitoring | Worker runner battle-tested | Stop/move/briefing alerts |
| 5 | F5 HITL upgrades | Approval/timeout/audit | "Suggest sell, confirm with human" |
| 6 | S2 rules engine + S3 Trade Plan | F7 structured output | Discipline checks + actionable advice |
| 7 | S4 paper trading + S5 data sources + S6 indicators | F6 Memory, F8 trace, F9 versioning | Strategy validation + analysis depth |
| 8 | F10 Task/Crew + F11 plugins | Ecosystem opens up | S8 watchlist, S9 multi-channel, S10 charts |

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
