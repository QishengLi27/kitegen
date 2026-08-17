"""demo.paper — Paper trading account, config, and persistence.

A virtual trading account isolated from the user's real portfolio
(demo/data/paper/). The engine enforces configurable rules:
T+1 (A-shares bought today sellable tomorrow), position caps, and
forced stop-losses.

Files (all gitignored under demo/data/):
    paper/account.json   virtual cash + positions + equity snapshots
    paper/config.json    editable config (restart or POST to apply)
    paper/trades.json    every simulated trade with decision rationale
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data" / "paper"


# ── Config ───────────────────────────────────────────────────────────────────


@dataclass
class PaperConfig:
    initial_capital: float = 500_000
    check_interval_min: int = 30
    max_position_pct: float = 0.20      # per-position cap (% of equity)
    stop_loss_pct: float = 0.08         # forced stop below cost basis
    t_plus_1: bool = True               # A-shares: buy today, sell tomorrow+
    fee_rate: float = 0.0003            # commission both ways
    enabled_symbols: list[str] | None = None  # None = real portfolio universe
    trading_hours_only: bool = True     # skip ticks outside trading hours


# Trading sessions in LOCAL time (assumes Beijing time — A-share + HK + US):
#   09:30-12:00  A-share + HK morning
#   13:00-16:00  A-share + HK afternoon
#   21:30-04:00  US market (EDT, overnight across midnight)
TRADING_SESSIONS = [
    ("09:30", "12:00"),
    ("13:00", "16:00"),
    ("21:30", "23:59"),
    ("00:00", "04:00"),
]


def is_trading_time(now: datetime | None = None) -> tuple[bool, str]:
    """Check whether it is currently a trading session on a weekday.

    Returns (in_session, reason_if_not). Weekends are never trading days.
    """
    now = now or datetime.now()
    if now.weekday() >= 5:  # Saturday / Sunday
        return False, "weekend"
    hm = now.strftime("%H:%M")
    for start, end in TRADING_SESSIONS:
        if start <= hm <= end:
            return True, ""
    return False, f"outside trading hours ({hm})"


# ── Models ───────────────────────────────────────────────────────────────────


@dataclass
class PaperPosition:
    symbol: str
    shares: int
    cost_basis: float
    buy_date: str                       # ISO date, for T+1


@dataclass
class PaperTrade:
    id: str
    timestamp: str
    action: str                         # "buy" | "sell"
    symbol: str
    price: float
    shares: int
    reason: str
    realized_pnl: float | None = None   # sells only


def _load_json(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default
    return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: temp file + os.replace. A crash mid-write must never
    # truncate the account file (which would silently reset the account).
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ── Config persistence ───────────────────────────────────────────────────────


def load_config() -> PaperConfig:
    data = _load_json(DATA_DIR / "config.json", {})
    return PaperConfig(
        initial_capital=float(data.get("initial_capital", 500_000)),
        check_interval_min=int(data.get("check_interval_min", 30)),
        max_position_pct=float(data.get("max_position_pct", 0.20)),
        stop_loss_pct=float(data.get("stop_loss_pct", 0.08)),
        t_plus_1=bool(data.get("t_plus_1", True)),
        fee_rate=float(data.get("fee_rate", 0.0003)),
        enabled_symbols=data.get("enabled_symbols"),
        trading_hours_only=bool(data.get("trading_hours_only", True)),
    )


def save_config(config: PaperConfig) -> None:
    _save_json(DATA_DIR / "config.json", asdict(config))


# ── Account persistence ──────────────────────────────────────────────────────


class PaperAccount:
    """Virtual account. All mutations go through buy()/sell() so the
    trade log and snapshots stay consistent."""

    def __init__(self):
        self.cash: float = 0.0
        self.positions: dict[str, PaperPosition] = {}
        self.trades: list[PaperTrade] = []
        self.snapshots: list[dict] = []     # equity curve points

    # -- persistence ----------------------------------------------------------

    @classmethod
    def load(cls) -> PaperAccount:
        acct = cls()
        data = _load_json(DATA_DIR / "account.json", {})
        config = load_config()
        acct.cash = float(data.get("cash", config.initial_capital))
        acct.positions = {
            s: PaperPosition(**p)
            for s, p in data.get("positions", {}).items()
        }
        acct.trades = [PaperTrade(**t) for t in data.get("trades", [])]
        acct.snapshots = data.get("snapshots", [])
        return acct

    def save(self) -> None:
        _save_json(DATA_DIR / "account.json", {
            "cash": self.cash,
            "positions": {s: asdict(p) for s, p in self.positions.items()},
            "trades": [asdict(t) for t in self.trades[-500:]],
            "snapshots": self.snapshots[-1000:],
        })
        # Also mirror the full trade log per spec
        _save_json(DATA_DIR / "trades.json", [asdict(t) for t in self.trades])

    def reset(self) -> None:
        config = load_config()
        self.cash = config.initial_capital
        self.positions = {}
        self.trades = []
        self.snapshots = []
        self.save()

    # -- valuation -------------------------------------------------------------

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(
            p.shares * prices.get(p.symbol, 0.0) for p in self.positions.values()
        )

    def snapshot(self, prices: dict[str, float]) -> None:
        self.snapshots.append({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "equity": round(self.equity(prices), 2),
        })

    # -- trading ---------------------------------------------------------------

    @staticmethod
    def _is_a_share(symbol: str) -> bool:
        return symbol.endswith(".SS") or symbol.endswith(".SZ")

    def sellable(self, symbol: str, today: date, t_plus_1: bool) -> tuple[bool, str]:
        """Check T+1. Returns (allowed, reason_if_blocked)."""
        pos = self.positions.get(symbol)
        if not pos:
            return False, f"no position in {symbol}"
        if not (t_plus_1 and self._is_a_share(symbol)):
            return True, ""
        if pos.buy_date >= today.isoformat():
            return False, f"T+1: {symbol} bought today, sellable tomorrow"
        return True, ""

    def buy(self, symbol: str, shares: int, price: float, reason: str,
            fee_rate: float) -> PaperTrade:
        """Simulate a buy. Raises ValueError if cash is insufficient."""
        if shares <= 0:
            raise ValueError("share count must be positive")
        cost = shares * price
        fee = cost * fee_rate
        if cost + fee > self.cash:
            raise ValueError(
                f"insufficient cash: need {cost + fee:.2f}, have {self.cash:.2f}"
            )
        self.cash -= cost + fee

        pos = self.positions.get(symbol)
        if pos:
            # Average up/down: weighted average cost, keep the OLDEST buy date
            total_shares = pos.shares + shares
            pos.cost_basis = (
                pos.cost_basis * pos.shares + price * shares
            ) / total_shares
            pos.shares = total_shares
        else:
            self.positions[symbol] = PaperPosition(
                symbol=symbol,
                shares=shares,
                cost_basis=price,
                buy_date=date.today().isoformat(),
            )

        trade = PaperTrade(
            id=uuid.uuid4().hex[:10],
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            action="buy",
            symbol=symbol,
            price=round(price, 4),
            shares=shares,
            reason=reason,
        )
        self.trades.append(trade)
        return trade

    def sell(self, symbol: str, shares: int, price: float, reason: str,
             fee_rate: float, t_plus_1: bool, today: date | None = None) -> PaperTrade:
        """Simulate a sell. T+1 is enforced here (A-shares only).

        Convention: ``shares <= 0`` means sell the ENTIRE position
        (this is how the decision agent expresses "sell all"). Positive
        values are clamped to the held quantity.
        """
        today = today or date.today()

        allowed, why = self.sellable(symbol, today, t_plus_1)
        if not allowed:
            raise ValueError(why)

        pos = self.positions[symbol]
        qty = pos.shares if shares <= 0 else min(shares, pos.shares)
        proceeds = qty * price
        fee = proceeds * fee_rate
        self.cash += proceeds - fee

        realized = (price - pos.cost_basis) * qty - fee
        pos.shares -= qty
        if pos.shares == 0:
            del self.positions[symbol]

        trade = PaperTrade(
            id=uuid.uuid4().hex[:10],
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            action="sell",
            symbol=symbol,
            price=round(price, 4),
            shares=qty,
            reason=reason,
            realized_pnl=round(realized, 2),
        )
        self.trades.append(trade)
        return trade


def get_universe(config: PaperConfig) -> list[str]:
    """Symbols the paper trader may trade. Default: the real portfolio's symbols."""
    if config.enabled_symbols:
        return [s.upper().strip() for s in config.enabled_symbols if s.strip()]
    from demo.portfolio import load_portfolio
    return list(load_portfolio("default").positions.keys())
