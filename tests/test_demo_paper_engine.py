"""Tests for demo.paper_engine — tick orchestration and rule enforcement."""

import asyncio

import pytest

from demo.paper import PaperAccount, PaperPosition


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Isolate paper data dir + patch signal/LLM/research dependencies."""
    monkeypatch.setattr("demo.paper.DATA_DIR", tmp_path)
    monkeypatch.setattr("demo.paper_engine.compute_signal", _FakeSignal())
    # Deterministic market-hours: everything tradable (tests run at any hour)
    monkeypatch.setattr(
        "demo.paper_engine.is_symbol_tradable",
        lambda symbol, now=None: (True, ""),
    )
    # Research always cache-hits — tests never touch the LLM or real cache files
    monkeypatch.setattr(
        "demo.paper_engine.get_cached_research",
        lambda symbol: {"report": "cached research report", "generated_at": "2026-08-17T10:00:00"},
    )
    monkeypatch.setattr("demo.paper_engine.cache_research", lambda symbol, report: None)
    # Explicit universe — otherwise get_universe falls back to the real
    # portfolio on disk, which tests must never depend on
    from demo.paper import PaperConfig, save_config
    save_config(PaperConfig(enabled_symbols=["TEST"]))
    return monkeypatch


class _FakeSignal:
    async def invoke(self, arguments, context):
        return "Signal: neutral\nConfidence: 0.0"


class _FakeTrader:
    """Fake decision agent — returns the configured decision set."""

    def __init__(self):
        self.output = None

    async def run(self, state):
        return {"output": self.output}


def _set_decisions(monkeypatch, decisions):
    trader = _FakeTrader()
    trader.output = decisions
    monkeypatch.setattr("demo.paper_engine.paper_trader", trader)
    return trader


def _set_prices(monkeypatch, mapping):
    def _fetch_all(symbols, timeout=15.0):
        return {s: mapping.get(s) for s in symbols}

    # monitor._fetch_all is a SYNC function (runs in threads) — match it
    monkeypatch.setattr("demo.paper_engine._fetch_all", _fetch_all)


def test_forced_stop_loss_liquidates_position(isolated, monkeypatch):
    """The forced stop rule sells the ENTIRE position via sell(shares=0)."""
    acct = PaperAccount()
    acct.cash = 0
    acct.positions["TEST"] = PaperPosition(
        symbol="TEST", shares=100, cost_basis=100.0,
        buy_date="2026-08-10",  # long ago — T+1 allows selling
    )
    acct.save()

    from demo.paper_engine import TradeDecisions
    _set_decisions(monkeypatch, TradeDecisions(decisions=[]))
    _set_prices(monkeypatch, {"TEST": {"price": 80.0, "chg": 0.0}})  # < 100*(1-0.08)

    from demo.paper_engine import paper_tick
    summary = asyncio.run(paper_tick(force=True))

    assert summary["status"] == "ok"
    sells = [t for t in summary["executed"] if t["action"] == "sell"]
    assert len(sells) == 1
    assert sells[0]["shares"] == 100  # entire position, not 0
    assert "forced stop-loss" in sells[0]["reason"]

    acct2 = PaperAccount.load()
    assert "TEST" not in acct2.positions


def test_zero_price_decision_is_blocked(isolated, monkeypatch):
    """A symbol with price 0 must not crash the tick or execute."""
    acct = PaperAccount()
    acct.cash = 100_000
    acct.save()

    from demo.paper_engine import TradeDecision, TradeDecisions
    _set_decisions(monkeypatch, TradeDecisions(decisions=[
        TradeDecision(symbol="TEST", action="buy", shares=100, reason="test"),
    ]))
    _set_prices(monkeypatch, {"TEST": {"price": 0.0, "chg": 0.0}})

    from demo.paper_engine import paper_tick
    summary = asyncio.run(paper_tick(force=True))

    assert summary["status"] == "ok"
    assert summary["executed"] == []
    assert any("invalid price" in b["reason"] for b in summary["blocked"])


def test_research_cache_miss_generates_and_caches(isolated, monkeypatch):
    """Cache miss → market_researcher runs → report cached + fed to the agent."""
    acct = PaperAccount()
    acct.cash = 100_000
    acct.save()

    from demo.paper_engine import TradeDecisions
    _set_decisions(monkeypatch, TradeDecisions(decisions=[]))
    _set_prices(monkeypatch, {"TEST": {"price": 10.0, "chg": 0.0}})

    cached_reports = []
    generated_inputs = []

    monkeypatch.setattr("demo.paper_engine.get_cached_research", lambda symbol: None)

    class _FakeResearcher:
        async def execute(self, state):
            generated_inputs.append(state["input"])
            return {"output": "fresh research: bullish regime, support 9.5"}

    monkeypatch.setattr("demo.paper_engine.market_researcher", _FakeResearcher())
    monkeypatch.setattr(
        "demo.paper_engine.cache_research",
        lambda symbol, report: cached_reports.append((symbol, report)),
    )

    from demo.paper_engine import paper_tick
    summary = asyncio.run(paper_tick(force=True))

    assert summary["status"] == "ok"
    assert len(generated_inputs) == 1           # researcher ran
    assert "TEST" in generated_inputs[0]        # asked about the right symbol
    assert cached_reports == [("TEST", "fresh research: bullish regime, support 9.5")]


def test_market_closed_blocks_decision(isolated, monkeypatch):
    """A symbol whose market is closed gets blocked, not traded."""
    acct = PaperAccount()
    acct.cash = 100_000
    acct.save()

    from demo.paper import PaperConfig, save_config
    save_config(PaperConfig(enabled_symbols=["000725.SZ"]))

    from demo.paper_engine import TradeDecision, TradeDecisions
    _set_decisions(monkeypatch, TradeDecisions(decisions=[
        TradeDecision(symbol="000725.SZ", action="buy", shares=100, reason="test"),
    ]))
    _set_prices(monkeypatch, {"000725.SZ": {"price": 6.08, "chg": 0.0}})
    monkeypatch.setattr(
        "demo.paper_engine.is_symbol_tradable",
        lambda symbol, now=None: (False, "CN market closed (22:00)"),
    )

    from demo.paper_engine import paper_tick
    summary = asyncio.run(paper_tick(force=True))

    assert summary["executed"] == []
    assert any("CN market closed" in b["reason"] for b in summary["blocked"])

    # Position must NOT exist
    acct2 = PaperAccount.load()
    assert "000725.SZ" not in acct2.positions


def test_tick_lock_serializes_concurrent_ticks(isolated, monkeypatch):
    """Manual + worker ticks overlap — the lock must serialize them."""
    import demo.paper_engine as engine

    call_order = []

    async def fake_tick(force=False):
        call_order.append("start")
        await asyncio.sleep(0.05)
        call_order.append("end")
        return {"status": "ok"}

    monkeypatch.setattr(engine, "_paper_tick_locked", fake_tick)

    async def run():
        await asyncio.gather(engine.paper_tick(True), engine.paper_tick(True))

    asyncio.run(run())
    # Serialized: start,end,start,end — never start,start (which would
    # mean the lock is not held)
    assert call_order == ["start", "end", "start", "end"]
