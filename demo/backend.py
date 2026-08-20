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
from typing import Any

import kitegen as kg
from demo.agents import get_pipeline, llm_tracker
from demo.portfolio import (
    Portfolio,
    Position,
    ensure_sample_portfolio,
    load_portfolio,
    save_portfolio,
)
from demo.tools import fetch_stock, resolve_symbol, set_risk_mode
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

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
    _asyncio.create_task(_autosave_usage())
    try:
        from demo.paper_engine import start_paper_trader
        _asyncio.create_task(start_paper_trader())
    except Exception:
        logging.getLogger("kitegen").exception("[paper] failed to start paper trader")


async def _autosave_usage() -> None:
    """Persist LLM usage history every 60s (survives restarts)."""
    import asyncio as _asyncio

    from demo.agents import llm_tracker

    while True:
        try:
            save_usage()  # sync — file write is fast, don't block the loop on it
        except Exception:
            logging.getLogger("kitegen").exception("[usage] autosave failed")
        await _asyncio.sleep(60)


def save_usage() -> None:
    """Write the token tracker to data/usage.json."""
    import json as _json

    from demo.agents import llm_tracker

    path = DATA_DIR / "usage.json"
    _save_json(path, llm_tracker.to_dict())


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


@app.get("/usage")
async def get_usage():
    """Aggregate LLM token usage and cost across the whole pipeline."""
    summary = llm_tracker.summary()
    return {
        **summary,
        "calls": len(llm_tracker._records),
    }


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

    # Pick the mode-specific pipeline. The request's risk_mode drives both
    # the agents' goals/personalities (baked into the pipeline) and the
    # tool thresholds (set per request via set_risk_mode in the stream).
    mode = str(data.get("risk_mode", "normal")).lower().strip()
    mode = mode if mode in ("conservative", "normal", "aggressive") else "normal"
    pipeline, pipeline_saver = get_pipeline(mode)

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

    # Force-refresh keywords bypass the research cache
    force_research = any(k in msg.lower() for k in (
        "重新研究", "重新分析", "深度分析", "deep dive", "force",
    ))

    # Same thread_id per session is intentional — kitegen's checkpoint merge
    # semantics keep saved state as the base while the new keys win.
    state = {
        "user_message": msg + sym_hint,
        "history": history_text,
        "symbol": sym,               # cache key for the research stage
        "force_research": force_research,
        "_node_history": [],
    }

    stage_labels = {
        "research": "🔎 Step 1 · Research",
        "strategy": "🎯 Step 2 · Strategy",
        "advice": "💼 Step 3 · Advice",
    }

    return StreamingResponse(
        _chat_event_stream(
            pipeline=pipeline,
            pipeline_saver=pipeline_saver,
            state=state,
            thread_id=tid,
            session=session,
            message=msg,
            symbol=sym,
            stage_labels=stage_labels,
            risk_mode=mode,
        ),
        media_type="text/event-stream",
    )


async def _chat_event_stream(
    pipeline: Any,
    pipeline_saver: Any,
    state: dict,
    thread_id: str,
    session: Session,
    message: str,
    symbol: str | None,
    stage_labels: dict[str, str],
    risk_mode: str = "normal",
):
    """Async generator that yields SSE events for the /chat endpoint.

    Forwards graph lifecycle events plus tool-call progress so the UI stays
    responsive during long LLM/tool round trips.
    """
    # Agent roles emitted by ToolCallEvent/ToolResultEvent do not match the
    # graph node names used by NodeStart/NodeEnd. Map them back so progress
    # updates attach to the correct stage in the UI.
    role_to_stage = {
        "stock researcher": "research",
        "trading strategist": "strategy",
        "personal portfolio analyst": "advice",
    }

    # Tools derive their thresholds from the risk-mode context var. Set it
    # in this request's task so every agent/tool call in the pipeline sees
    # the mode the user picked (contextvars are task-local, so this never
    # leaks into other requests).
    set_risk_mode(risk_mode)

    collected = ""
    try:
        async for event in pipeline.invoke_stream(state, thread_id=thread_id):
            match event:
                case kg.NodeStart(node=n):
                    label = stage_labels.get(n, n)
                    yield f"data: {json.dumps({'type': 'stage_started', 'stage': n, 'label': label})}\n\n"
                case kg.NodeEnd(node=n):
                    yield f"data: {json.dumps({'type': 'stage_done', 'stage': n})}\n\n"
                case kg.ToolCallEvent(node=n, tool=t, arguments=_):
                    # Stream live progress so the UI does not feel frozen
                    # during long tool-call round trips.
                    stage = role_to_stage.get(n, n)
                    yield f"data: {json.dumps({'type': 'progress', 'stage': stage, 'status': 'calling', 'tool': t, 'detail': f'Calling {t}...'})}\n\n"
                case kg.ToolResultEvent(node=n, tool=t, result=_):
                    stage = role_to_stage.get(n, n)
                    yield f"data: {json.dumps({'type': 'progress', 'stage': stage, 'status': 'done', 'tool': t, 'detail': f'{t} returned'})}\n\n"
                case kg.Custom(data=d):
                    if isinstance(d, dict) and d.get("type") == "stage":
                        yield f"data: {json.dumps({'type': 'stage_output', 'stage': d['stage'], 'content': d['content']})}\n\n"
                case kg.NodeError(node=n, error=e):
                    yield f"data: {json.dumps({'type': 'error', 'message': f'{n}: {e}'})}\n\n"
                case kg.Complete():
                    pass

        # Load final state and stream the advice as tokens
        final_state = await pipeline_saver.load(thread_id)
        if final_state and final_state.get("output"):
            collected = final_state["output"]
            # Split preserving newlines/spaces — the advice is markdown and
            # the frontend renders it; naive word-splitting would destroy
            # heading/table syntax.
            for piece in re.split(r"(\s+)", collected):
                if piece:
                    yield f"data: {json.dumps({'type': 'token', 'content': piece})}\n\n"

        session.history.append({"role": "user", "content": message})
        if collected:
            session.history.append({"role": "assistant", "content": collected})

        # Persist usage immediately — no waiting for the 60s autosave
        try:
            save_usage()
        except Exception:
            pass

        yield f"data: {json.dumps({'type': 'done', 'symbol': symbol})}\n\n"
    except Exception as e:
        err = str(e)
        # Friendly message for missing API key
        if "api_key" in err.lower() or "credentials" in err.lower():
            err = "No LLM API key configured. Set OPENAI_API_KEY or LLM_API_KEY environment variable."
        yield f"data: {json.dumps({'type': 'error', 'message': err})}\n\n"


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
    from demo.monitor import _fetch_all  # parallel + per-symbol timeout

    portfolio = load_portfolio("default")
    fetched = _fetch_all(list(portfolio.positions.keys()))
    prices = {
        symbol: (data["price"] if data else 0.0)
        for symbol, data in fetched.items()
    }

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
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit,
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
    stop_loss = data.get("stop_loss")
    take_profit = data.get("take_profit")

    if not symbol or shares <= 0 or cost_basis <= 0:
        return {"status": "error", "message": "Invalid position data"}

    portfolio = load_portfolio("default")
    portfolio.positions[symbol] = Position(
        symbol=symbol,
        shares=shares,
        cost_basis=cost_basis,
        stop_loss=float(stop_loss) if stop_loss not in (None, "") else None,
        take_profit=float(take_profit) if take_profit not in (None, "") else None,
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


# ── Paper trading endpoints ────────────────────────────────────────────────


@app.get("/paper")
async def get_paper_account():
    """Paper account summary: equity curve, positions, recent trades."""
    from demo.paper import PaperAccount, load_config

    account = PaperAccount.load()
    return {
        "cash": round(account.cash, 2),
        "positions": [
            {"symbol": p.symbol, "shares": p.shares,
             "cost_basis": p.cost_basis, "buy_date": p.buy_date}
            for p in account.positions.values()
        ],
        "snapshots": account.snapshots[-100:],
        "trades": [t.__dict__ for t in account.trades[-50:]],
        "config": load_config().__dict__,
    }


@app.post("/paper/tick")
async def run_paper_tick(force: bool = False):
    """Manually trigger one paper trading tick (also runs on the schedule).

    With ?force=1, trading-hours restrictions are bypassed (for testing).
    """
    from demo.paper_engine import paper_tick

    try:
        summary = await paper_tick(force=force)
        return summary
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/paper/config")
async def update_paper_config(request: Request):
    """Update the paper trading config. Takes effect next tick.

    risk_mode may be sent alone; when it changes, the derived rule params
    (max_position_pct, stop_loss_pct) adopt the mode's recommended profile
    unless the request provides them explicitly.
    """
    from demo.paper import load_config, save_config, update_config_from_dict

    data = await request.json()
    current = load_config()
    try:
        updated = update_config_from_dict(current, data)
    except (ValueError, TypeError) as e:
        return {"status": "error", "message": str(e)}

    # Validate ranges — a bad config (interval=0, pct>1, negative capital)
    # would busy-loop the worker or break the money math
    errors = []
    if updated.initial_capital <= 0:
        errors.append("initial_capital must be positive")
    if updated.check_interval_min < 1:
        errors.append("check_interval_min must be >= 1")
    if not (0 < updated.max_position_pct <= 1):
        errors.append("max_position_pct must be in (0, 1]")
    if not (0 < updated.stop_loss_pct <= 1):
        errors.append("stop_loss_pct must be in (0, 1]")
    if not (0 <= updated.fee_rate < 0.1):
        errors.append("fee_rate must be in [0, 0.1)")
    if errors:
        return {"status": "error", "message": "; ".join(errors)}

    save_config(updated)
    return {"status": "ok", "config": updated.__dict__}


@app.post("/paper/reset")
async def reset_paper_account():
    """Reset the paper account to the configured initial capital."""
    from demo.paper import PaperAccount

    account = PaperAccount.load()
    account.reset()
    return {"status": "ok"}


# Static files are served by Nginx via docker-compose; the backend no longer
# mounts /static. Keep this section empty so the commented block below stays
# available for local development without the StaticFiles import.
# To re-enable backend static serving, add back:
#   from fastapi.staticfiles import StaticFiles
# and uncomment the following lines.
# static_dir = _os.path.join(_os.path.dirname(__file__), "static")
# if _os.path.isdir(static_dir):
#     app.mount("/static", StaticFiles(directory=static_dir, html=True), name="static")
