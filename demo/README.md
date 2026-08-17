# Stock Analyst Demo

A personal stock analyst built on kitegen — the framework's flagship example and primary test vehicle.

> ⚠️ This is an analysis and decision-support tool, **not financial advice** and not a brokerage.

## What it does

Ask questions about any stock (US / HK / China A-share, in Chinese or English). Each question runs a **3-agent pipeline**:

```
🔎 Research (market_researcher)  →  🎯 Strategy (trading_strategist)  →  💼 Advice (portfolio_analyst)
   price + fundamentals + techs      trading plan with levels            personalized to YOUR positions
```

Every answer follows a mandatory bidirectional structure: **exit scenario** (stop-loss / take-profit), **add-on scenario** (trigger conditions + computed position size), and **do-nothing zone**.

On top of chat:

| Feature | What it does |
|---------|-------------|
| **Portfolio** | Record positions in natural language ("我买了2500股000725.SZ，成本6.043"), persisted to `data/` |
| **Stop-loss / take-profit** | Set per-position levels via chat; the agent suggests levels from technicals |
| **Market monitor** | Background worker checks stops, take-profits, and ±5% daily moves |
| **Daily briefing** | Auto-generated after 08:50 each day (or on demand) — full portfolio table with signals |
| **Streaming UI** | Stage-by-stage progress, real markdown rendering, stop button, alerts panel |

## Setup

```bash
cd kitegen                      # repo root
python -m venv venv && source venv/bin/activate
pip install -e .                # the framework
pip install -r demo/requirements.txt
cp demo/.env.example demo/.env  # add your API key + model
```

`.env` configuration:

```bash
LLM_API_KEY=sk-...                    # DeepSeek or any OpenAI-compatible key
LLM_API_BASE=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash           # query options: curl $LLM_API_BASE/models -H "Authorization: Bearer $LLM_API_KEY"

# Monitor (all optional)
MONITOR_INTERVAL=300                  # check every 5 min
MONITOR_MOVE_THRESHOLD=5              # daily-move alert %
MONITOR_BRIEFING_TIME=08:50           # briefing time
WEBHOOK_URL=...                       # alerts also POST here (DingTalk/Feishu/ServerChan/…)
```

## Run

```bash
# Terminal 1 — backend (FastAPI + monitor worker)
PYTHONPATH=. python -m uvicorn demo.backend:app --port 8000

# Terminal 2 — frontend
cd demo/frontend && npm install && npm run dev
# open http://localhost:5173
```

## Data sources

| Market | Source | Notes |
|--------|--------|-------|
| US | YahooQuery | falls back to Tencent (`usAAPL.OQ`) when Yahoo is unreachable |
| HK / A-share | Tencent free APIs | quotes + 260-day history + smartbox name search |
| Company names | Tencent smartbox | any Chinese/English name resolves to a symbol (京东方 → 000725.SZ) |

Symbol resolution order: direct code → static name map → smartbox search.

## API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/chat` | POST | Stream the 3-agent pipeline via SSE: `{"message": "...", "thread_id": "..."}` |
| `/portfolio` | GET | Positions with live prices and P&L |
| `/portfolio/positions` | POST | Add/update a position |
| `/portfolio/positions/{symbol}` | DELETE | Remove a position |
| `/alerts` | GET | Monitor alerts, unacknowledged first |
| `/alerts/{id}/ack` | POST | Acknowledge an alert |
| `/briefing` | GET | Latest briefing |
| `/briefing` | POST | Generate now (`?force=1` to regenerate) |
| `/reset` | POST | Reset a chat session's history |

## Project structure

```
demo/
  agents.py      3 agents + pipeline graph (research → strategy → advice)
  tools.py       market data (Tencent/YahooQuery), technicals, position sizing
  portfolio.py   Portfolio/Position models, JSON persistence
  monitor.py     alert checks, dedup, daily briefing, kg.to_worker loop
  backend.py     FastAPI: chat SSE + portfolio/alerts/briefing endpoints
  data/          personal portfolio + alerts (gitignored)
  frontend/      React app (Vite) — streaming chat, stage stepper, alerts panel
  static/        legacy static frontend (reference)
```

## Framework features exercised

- `Agent` with tool-calling loop (all three agents)
- `@tool` decorator with type-hint schema inference
- `Graph` composing agents as nodes, streaming with cancel, checkpoint merge semantics
- `stream_event()` for stage outputs, `to_worker()` for the monitor
- `interrupt()`/`resume()` available for human-in-the-loop nodes
