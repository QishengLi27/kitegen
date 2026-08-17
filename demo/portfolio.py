"""Portfolio model and persistence for the kitegen stock analyst demo.

Stores positions in a JSON file so the agent can reason about your actual
holdings, cost basis, and targets.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Position:
    """A single portfolio holding."""

    symbol: str
    shares: float
    cost_basis: float
    target_allocation: float = 0.0
    stop_loss: float | None = None
    take_profit: float | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class Portfolio:
    """A portfolio with cash and positions."""

    cash: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    currency: str = "USD"

    def equity(self, prices: dict[str, float]) -> float:
        """Total portfolio value at current prices."""
        position_value = sum(
            p.shares * prices.get(p.symbol, 0.0) for p in self.positions.values()
        )
        return self.cash + position_value

    def position_pnl(self, symbol: str, price: float) -> dict[str, Any]:
        """Return P&L metrics for a single position."""
        pos = self.positions.get(symbol)
        if not pos:
            return {}
        market_value = pos.shares * price
        cost = pos.shares * pos.cost_basis
        return {
            "symbol": symbol,
            "shares": pos.shares,
            "cost_basis": pos.cost_basis,
            "current_price": price,
            "market_value": round(market_value, 2),
            "unrealized_pnl": round(market_value - cost, 2),
            "unrealized_pnl_pct": round((market_value - cost) / cost * 100, 2) if cost else 0.0,
            "weight": 0.0,
        }

    def all_pnl(self, prices: dict[str, float]) -> dict[str, dict[str, Any]]:
        """Return P&L metrics for all positions."""
        result = {}
        total_equity = self.equity(prices)
        for symbol, pos in self.positions.items():
            price = prices.get(symbol, 0.0)
            pnl = self.position_pnl(symbol, price)
            if total_equity:
                pnl["weight"] = round(pnl.get("market_value", 0) / total_equity, 4)
            result[symbol] = pnl
        return result

    def biggest_losers(self, prices: dict[str, float], limit: int = 5) -> list[dict[str, Any]]:
        """Return positions with the largest unrealized losses."""
        pnls = self.all_pnl(prices)
        sorted_by_loss = sorted(
            pnls.values(),
            key=lambda x: x.get("unrealized_pnl_pct", 0),
        )
        return sorted_by_loss[:limit]


# ── Persistence ──────────────────────────────────────────────────────────────


def _portfolio_path(portfolio_id: str = "default") -> Path:
    path = Path(__file__).parent / "data" / f"{portfolio_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _position_to_dict(p: Position) -> dict[str, Any]:
    return {
        "symbol": p.symbol,
        "shares": p.shares,
        "cost_basis": p.cost_basis,
        "target_allocation": p.target_allocation,
        "stop_loss": p.stop_loss,
        "take_profit": p.take_profit,
        "tags": p.tags,
    }


def _position_from_dict(d: dict[str, Any]) -> Position:
    return Position(
        symbol=d["symbol"],
        shares=float(d["shares"]),
        cost_basis=float(d["cost_basis"]),
        target_allocation=float(d.get("target_allocation", 0.0)),
        stop_loss=float(d["stop_loss"]) if d.get("stop_loss") is not None else None,
        take_profit=float(d["take_profit"]) if d.get("take_profit") is not None else None,
        tags=list(d.get("tags", [])),
    )


def save_portfolio(portfolio: Portfolio, portfolio_id: str = "default") -> None:
    """Save a portfolio to JSON."""
    path = _portfolio_path(portfolio_id)
    data = {
        "cash": portfolio.cash,
        "currency": portfolio.currency,
        "positions": {
            symbol: _position_to_dict(p) for symbol, p in portfolio.positions.items()
        },
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_portfolio(portfolio_id: str = "default") -> Portfolio:
    """Load a portfolio from JSON, creating a default one if missing."""
    path = _portfolio_path(portfolio_id)
    if not path.exists():
        portfolio = Portfolio()
        save_portfolio(portfolio, portfolio_id)
        return portfolio

    data = json.loads(path.read_text(encoding="utf-8"))
    return Portfolio(
        cash=float(data.get("cash", 0.0)),
        currency=data.get("currency", "USD"),
        positions={
            symbol: _position_from_dict(p)
            for symbol, p in data.get("positions", {}).items()
        },
    )


def ensure_sample_portfolio(portfolio_id: str = "default") -> Portfolio:
    """Create a sample portfolio if one does not exist."""
    path = _portfolio_path(portfolio_id)
    if path.exists():
        return load_portfolio(portfolio_id)

    portfolio = Portfolio(
        cash=10000.0,
        positions={
            "AAPL": Position(symbol="AAPL", shares=20.0, cost_basis=185.0, target_allocation=0.15),
            "NVDA": Position(symbol="NVDA", shares=15.0, cost_basis=125.0, target_allocation=0.20),
            "0700.HK": Position(symbol="0700.HK", shares=200.0, cost_basis=380.0, target_allocation=0.10),
            "600519.SS": Position(symbol="600519.SS", shares=5.0, cost_basis=1700.0, target_allocation=0.10),
        },
    )
    save_portfolio(portfolio, portfolio_id)
    return portfolio
