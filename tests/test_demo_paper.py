"""Tests for demo.paper — virtual account, T+1, config."""

import json
from datetime import date

import pytest

from demo.paper import PaperAccount, PaperConfig, PaperPosition


def _fresh_account(cash=100_000.0) -> PaperAccount:
    acct = PaperAccount()
    acct.cash = cash
    return acct


def test_buy_updates_cash_and_position():
    acct = _fresh_account()
    t = acct.buy("AAPL", 100, 150.0, "test buy", fee_rate=0.0)

    assert acct.cash == 85_000.0
    assert acct.positions["AAPL"].shares == 100
    assert acct.positions["AAPL"].cost_basis == 150.0
    assert t.action == "buy" and t.shares == 100
    assert len(acct.trades) == 1


def test_buy_rejects_insufficient_cash():
    acct = _fresh_account(cash=1000.0)
    with pytest.raises(ValueError, match="insufficient cash"):
        acct.buy("AAPL", 100, 150.0, "too big", fee_rate=0.0)


def test_buy_averages_cost_basis():
    acct = _fresh_account()
    acct.buy("AAPL", 100, 100.0, "first", fee_rate=0.0)
    acct.buy("AAPL", 100, 200.0, "second", fee_rate=0.0)

    pos = acct.positions["AAPL"]
    assert pos.shares == 200
    assert pos.cost_basis == 150.0  # weighted average


def test_sell_computes_realized_pnl():
    acct = _fresh_account()
    acct.buy("AAPL", 100, 150.0, "buy", fee_rate=0.0)
    tomorrow = date.today() + __import__("datetime").timedelta(days=1)
    t = acct.sell("AAPL", 100, 180.0, "sell", fee_rate=0.0,
                  t_plus_1=True, today=tomorrow)

    assert t.realized_pnl == 3000.0
    assert "AAPL" not in acct.positions
    assert acct.cash == 100_000.0 - 15_000.0 + 18_000.0


def test_t_plus_1_blocks_same_day_sell_for_a_shares():
    import datetime

    acct = _fresh_account()
    acct.buy("600519.SS", 10, 1300.0, "buy", fee_rate=0.0)

    today = date.today()
    with pytest.raises(ValueError, match="T\\+1"):
        acct.sell("600519.SS", 10, 1400.0, "sell", fee_rate=0.0,
                  t_plus_1=True, today=today)  # same day

    # Next day works
    t = acct.sell("600519.SS", 10, 1400.0, "sell", fee_rate=0.0,
                  t_plus_1=True, today=today + datetime.timedelta(days=1))
    assert t.realized_pnl == 1000.0


def test_t_plus_1_does_not_apply_to_us_stocks():
    acct = _fresh_account()
    acct.buy("AAPL", 10, 150.0, "buy", fee_rate=0.0)

    # Same-day sell of a US stock is allowed even with T+1 enabled
    t = acct.sell("AAPL", 10, 160.0, "sell", fee_rate=0.0,
                  t_plus_1=True, today=date.today())
    assert t.realized_pnl == 100.0


def test_sell_clamps_to_held_quantity():
    acct = _fresh_account()
    acct.buy("AAPL", 10, 150.0, "buy", fee_rate=0.0)
    t = acct.sell("AAPL", 999, 160.0, "sell all", fee_rate=0.0,
                  t_plus_1=False, today=date.today())
    assert t.shares == 10
    assert "AAPL" not in acct.positions


def test_sell_zero_means_entire_position():
    """shares <= 0 sells the whole position (agent convention)."""
    acct = _fresh_account()
    acct.buy("AAPL", 10, 150.0, "buy", fee_rate=0.0)
    t = acct.sell("AAPL", 0, 160.0, "sell all", fee_rate=0.0,
                  t_plus_1=False, today=date.today())
    assert t.shares == 10
    assert "AAPL" not in acct.positions


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("demo.paper.DATA_DIR", tmp_path)

    config = PaperConfig(initial_capital=123_456, check_interval_min=5,
                         t_plus_1=False, enabled_symbols=["AAPL", "0700.HK"])
    from demo.paper import save_config, load_config
    save_config(config)

    loaded = load_config()
    assert loaded.initial_capital == 123_456
    assert loaded.check_interval_min == 5
    assert loaded.t_plus_1 is False
    assert loaded.enabled_symbols == ["AAPL", "0700.HK"]


def test_account_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("demo.paper.DATA_DIR", tmp_path)

    acct = _fresh_account()
    acct.buy("AAPL", 10, 150.0, "buy", fee_rate=0.0)
    acct.snapshot({"AAPL": 155.0})
    acct.save()

    loaded = PaperAccount.load()
    assert loaded.cash == acct.cash
    assert loaded.positions["AAPL"].shares == 10
    assert len(loaded.trades) == 1
    assert len(loaded.snapshots) == 1


# ── Trading hours ────────────────────────────────────────────────────────────


def test_trading_time_sessions():
    from datetime import datetime

    from demo.paper import is_trading_time

    # 2026-08-17 is a Monday
    in_session, _ = is_trading_time(datetime(2026, 8, 17, 10, 0))   # A-share morning
    assert in_session is True
    in_session, _ = is_trading_time(datetime(2026, 8, 17, 14, 30))  # A-share afternoon
    assert in_session is True
    in_session, _ = is_trading_time(datetime(2026, 8, 17, 22, 0))   # US evening
    assert in_session is True
    in_session, _ = is_trading_time(datetime(2026, 8, 18, 2, 0))    # US overnight
    assert in_session is True


def test_trading_time_outside_sessions():
    from datetime import datetime

    from demo.paper import is_trading_time

    for hour, minute in [(5, 0), (7, 0), (8, 30), (12, 30), (17, 0), (19, 0), (20, 0)]:
        in_session, why = is_trading_time(datetime(2026, 8, 17, hour, minute))
        assert in_session is False, f"{hour}:{minute} should be outside sessions"
        assert "outside trading hours" in why


def test_trading_time_weekend():
    from datetime import datetime

    from demo.paper import is_trading_time

    # 2026-08-15 is a Saturday, 10:00 would otherwise be a session
    in_session, why = is_trading_time(datetime(2026, 8, 15, 10, 0))
    assert in_session is False
    assert why == "weekend"


def test_trading_time_session_boundaries():
    from datetime import datetime

    from demo.paper import is_trading_time

    in_session, _ = is_trading_time(datetime(2026, 8, 17, 9, 30))   # open
    assert in_session is True
    in_session, _ = is_trading_time(datetime(2026, 8, 17, 12, 0))   # close (inclusive)
    assert in_session is True
    in_session, _ = is_trading_time(datetime(2026, 8, 17, 12, 1))   # lunch break
    assert in_session is False


def test_is_symbol_tradable_per_market():
    """Per-symbol sessions: A-share can't trade during US hours, etc."""
    from datetime import datetime

    from demo.paper import is_symbol_tradable

    # 22:00 Beijing — US open, A-share/HK closed
    ok, _ = is_symbol_tradable("AAPL", datetime(2026, 8, 17, 22, 0))
    assert ok is True
    ok, why = is_symbol_tradable("000725.SZ", datetime(2026, 8, 17, 22, 0))
    assert ok is False and "CN market closed" in why
    ok, why = is_symbol_tradable("0700.HK", datetime(2026, 8, 17, 22, 0))
    assert ok is False and "HK market closed" in why

    # 10:00 — A-share + HK open, US closed
    ok, _ = is_symbol_tradable("600519.SS", datetime(2026, 8, 17, 10, 0))
    assert ok is True
    ok, why = is_symbol_tradable("AAPL", datetime(2026, 8, 17, 10, 0))
    assert ok is False and "US market closed" in why

    # 15:30 — HK afternoon open, A-share closed
    ok, _ = is_symbol_tradable("0700.HK", datetime(2026, 8, 17, 15, 30))
    assert ok is True
    ok, _ = is_symbol_tradable("000725.SZ", datetime(2026, 8, 17, 15, 30))
    assert ok is False


def test_rebuild_trader_memory_from_trades(tmp_path, monkeypatch):
    """Restart simulation: memory is rebuilt from the persisted trade ledger."""
    monkeypatch.setattr("demo.paper.DATA_DIR", tmp_path)

    # Make some trades in a previous "session"
    acct = _fresh_account(cash=1_000_000)
    acct.buy("NVDA", 100, 225.0, "strong signal", fee_rate=0.0)
    acct.buy("300750.SZ", 100, 400.0, "bullish regime", fee_rate=0.0)
    acct.save()

    from demo.paper_engine import paper_trader, rebuild_trader_memory

    # Fresh memory (as after a restart)
    paper_trader.memory.clear()
    assert len(paper_trader.memory) == 0

    rebuild_trader_memory(limit=4)

    msgs = paper_trader.memory.get()
    assert len(msgs) == 4  # 2 trades × (user + assistant)
    combined = " ".join(m.content for m in msgs)
    assert "BUY NVDA" in combined
    assert "300750.SZ" in combined
    assert "strong signal" in combined

    paper_trader.memory.clear()
