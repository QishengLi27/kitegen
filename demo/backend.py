"""Personal Stock Analyst — kitegen demo.

The agent reads your portfolio, fetches price/fundamental/technical data,
and gives trading suggestions for the coming days and weeks.

Setup:
    cp demo/.env.example demo/.env   # add your API keys
    pip install -r demo/requirements.txt

Run:
    PYTHONPATH=. python -m uvicorn demo.backend:app --port 8000
"""

# Load .env before anything else
import os as _os
from pathlib import Path as _Path

try:
    from dotenv import load_dotenv as _load_dotenv

    _env = _Path(__file__).parent / ".env"
    if _env.exists():
        _load_dotenv(_env)
except ImportError:
    pass

import json
import logging
import re
from dataclasses import dataclass, field

import kitegen as kg
from demo.agents import pipeline, pipeline_saver
from demo.portfolio import (
    Portfolio,
    Position,
    ensure_sample_portfolio,
    load_portfolio,
    save_portfolio,
)
from demo.tools import fetch_stock, resolve_symbol
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

# Market monitoring
from demo.monitor import _save_json, load_alerts, start_monitor, DATA_DIR

# Enable kitegen debug logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("kitegen").setLevel(logging.DEBUG)

# Ensure a sample portfolio exists for first-time users
ensure_sample_portfolio("default")

# ── Session store ─────────────────────────────────────────────────────────


@dataclass
class Session:
    history: list[dict] = field(default_factory=list)


sessions: dict[str, Session] = {}


# ── FastAPI ───────────────────────────────────────────────────────────────

app = FastAPI(title="Personal Stock Analyst", version="0.4.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _start_monitor():
    """Start the market monitoring background task (alerts + daily briefing)."""
    import asyncio as _asyncio

    _asyncio.create_task(start_monitor())


# ── Alerts endpoints ──────────────────────────────────────────────────────


@app.get("/alerts")
async def get_alerts():
    """Recent alerts, unacknowledged first."""
    alerts = load_alerts()
    unacked = sorted(
        (a for a in alerts if not a.get("acknowledged")),
        key=lambda a: a.get("timestamp", ""),
        reverse=True,
    )
    acked = [a for a in alerts if a.get("acknowledged")]
    return {"alerts": unacked + acked}


@app.post("/alerts/{alert_id}/ack")
async def ack_alert(alert_id: str):
    """Acknowledge (mark read) one alert."""
    alerts = load_alerts()
    for alert in alerts:
        if alert.get("id") == alert_id:
            alert["acknowledged"] = True
            break
    _save_json(DATA_DIR / "alerts.json", alerts)
    return {"status": "ok"}


@app.get("/briefing")
async def get_latest_briefing():
    """Return the latest market briefing (full content)."""
    alerts = load_alerts()
    for alert in alerts:
        if alert.get("kind") == "briefing":
            return alert
    return {"status": "not_found"}


@app.post("/briefing")
async def trigger_briefing(force: bool = False):
    """Generate a market briefing on demand (shared with the scheduled job).

    Returns the existing briefing if already generated today; ?force=1 regenerates.
    """
    from demo.monitor import generate_briefing

    try:
        alert = await generate_briefing(force=force)
        if alert is None:
            # Already generated — return today's existing briefing
            for existing in load_alerts():
                if existing.get("kind") == "briefing":
                    return {
                        "status": "already_generated",
                        "id": existing["id"],
                        "message": existing["message"],
                    }
            return {"status": "not_found"}
        return {"status": "ok", "id": alert.id, "message": alert.message}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/chat")
async def chat(request: Request):
    """Stream agent response via SSE. Send {"message": "...", "thread_id": "..."}."""
    data = await request.json()
    msg = str(data.get("message", "")).strip()
    tid = data.get("thread_id", "default")

    if not msg:
        async def empty():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Empty message'})}\n\n"

        return StreamingResponse(empty(), media_type="text/event-stream")

    # Maintain session history
    session = sessions.get(tid)
    if session is None:
        session = Session()
        sessions[tid] = session

    # Resolve any stock symbol mentioned, add to message
    sym = resolve_symbol(msg)
    sym_hint = f"\n[Resolved stock symbol from query: {sym}]" if sym else ""

    # Conversation history as separate context — the current question is
    # the only thing that matters for THIS run
    history_text = ""
    if session.history:
        recent = session.history[-4:]  # last 2 exchanges, brief
        history_text = "Previous conversation (context only):\n" + "\n".join(
            f"{'User' if h['role'] == 'user' else 'Assistant'}: {h['content'][:200]}"
            for h in recent
        )

    # Same thread_id per session is intentional — kitegen's checkpoint merge
    # semantics keep saved state as the base while the new keys win.
    state = {
        "user_message": msg + sym_hint,
        "history": history_text,
        "_node_history": [],
    }

    STAGE_LABELS = {
        "research": "🔎 Step 1 · Research",
        "strategy": "🎯 Step 2 · Strategy",
        "advice": "💼 Step 3 · Advice",
    }

    async def event_stream():
        collected = ""
        try:
            async for event in pipeline.invoke_stream(state, thread_id=tid):
                match event:
                    case kg.NodeStart(node=n):
                        label = STAGE_LABELS.get(n, n)
                        yield f"data: {json.dumps({'type': 'stage_started', 'stage': n, 'label': label})}\n\n"
                    case kg.NodeEnd(node=n):
                        yield f"data: {json.dumps({'type': 'stage_done', 'stage': n})}\n\n"
                    case kg.Custom(data=d):
                        if isinstance(d, dict) and d.get("type") == "stage":
                            yield f"data: {json.dumps({'type': 'stage_output', 'stage': d['stage'], 'content': d['content']})}\n\n"
                    case kg.NodeError(node=n, error=e):
                        yield f"data: {json.dumps({'type': 'error', 'message': f'{n}: {e}'})}\n\n"
                    case kg.Complete():
                        pass

            # Load final state and stream the advice as tokens
            final_state = await pipeline_saver.load(tid)
            if final_state and final_state.get("output"):
                collected = final_state["output"]
                # Split preserving newlines/spaces — the advice is markdown and
                # the frontend renders it; naive word-splitting would destroy
                # heading/table syntax.
                for piece in re.split(r"(\s+)", collected):
                    if piece:
                        yield f"data: {json.dumps({'type': 'token', 'content': piece})}\n\n"

            session.history.append({"role": "user", "content": msg})
            if collected:
                session.history.append({"role": "assistant", "content": collected})

            yield f"data: {json.dumps({'type': 'done', 'symbol': sym})}\n\n"
        except Exception as e:
            err = str(e)
            # Friendly message for missing API key
            if "api_key" in err.lower() or "credentials" in err.lower():
                err = "No LLM API key configured. Set OPENAI_API_KEY or LLM_API_KEY environment variable."
            yield f"data: {json.dumps({'type': 'error', 'message': err})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/reset")
async def reset(request: Request):
    data = await request.json()
    sessions.pop(data.get("thread_id", "default"), None)
    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Portfolio endpoints ───────────────────────────────────────────────────


@app.get("/portfolio")
async def get_portfolio():
    """Return the current portfolio with live prices."""
    portfolio = load_portfolio("default")
    prices = {}
    for symbol in portfolio.positions:
        data = fetch_stock(symbol)
        if data:
            prices[symbol] = data["price"]
        else:
            prices[symbol] = 0.0

    pnls = portfolio.all_pnl(prices)
    return {
        "cash": portfolio.cash,
        "equity": portfolio.equity(prices),
        "positions": [
            {
                "symbol": symbol,
                "shares": p.shares,
                "cost_basis": p.cost_basis,
                "current_price": prices.get(symbol, 0),
                **pnls.get(symbol, {}),
            }
            for symbol, p in portfolio.positions.items()
        ],
    }


@app.post("/portfolio/positions")
async def add_position(request: Request):
    """Add or update a position. Body: {symbol, shares, cost_basis}."""
    data = await request.json()
    symbol = str(data.get("symbol", "")).upper().strip()
    shares = float(data.get("shares", 0))
    cost_basis = float(data.get("cost_basis", 0))

    if not symbol or shares <= 0 or cost_basis <= 0:
        return {"status": "error", "message": "Invalid position data"}

    portfolio = load_portfolio("default")
    portfolio.positions[symbol] = Position(
        symbol=symbol,
        shares=shares,
        cost_basis=cost_basis,
    )
    save_portfolio(portfolio, "default")
    return {"status": "ok", "symbol": symbol}


@app.delete("/portfolio/positions/{symbol}")
async def remove_position(symbol: str):
    """Remove a position from the portfolio."""
    portfolio = load_portfolio("default")
    if symbol in portfolio.positions:
        del portfolio.positions[symbol]
        save_portfolio(portfolio, "default")
    return {"status": "ok"}


# ── Static files ──────────────────────────────────────────────────────────

static_dir = _os.path.join(_os.path.dirname(__file__), "static")
if _os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir, html=True), name="static")
