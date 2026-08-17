"""Stock data tools for the kitegen analyst demo.

Supports YahooQuery (US/global) and Tencent (China A-shares + HK) with
free endpoints.
"""

from __future__ import annotations

import re
from typing import Any

import requests


def _to_tencent_code(symbol: str) -> str | None:
    """Convert Yahoo-style symbols to Tencent codes: 600519.SS -> sh600519."""
    if symbol.endswith(".SS"):
        return "sh" + symbol[:-3]
    if symbol.endswith(".SZ"):
        return "sz" + symbol[:-3]
    if symbol.endswith(".HK"):
        # Tencent uses 5-digit zero-padded HK codes: 0700.HK -> hk00700
        digits = symbol[:-3]
        return "hk" + digits.zfill(5)
    if symbol.isalpha():
        return "us" + symbol
    return None


def _fetch_tencent(symbol: str) -> dict[str, Any]:
    """Fetch stock data from Tencent's free API (best for China A-shares + HK)."""
    code = _to_tencent_code(symbol)
    if not code:
        raise ValueError(f"Cannot convert {symbol} to Tencent code")

    quote_url = "http://qt.gtimg.cn/q=" + code
    r = requests.get(quote_url, timeout=10)
    r.encoding = "gbk"
    text = r.text.strip()

    prefix = f'v_{code}="'
    if not text.startswith(prefix):
        raise ValueError(f"Unexpected Tencent quote response: {text[:100]}")

    parts = text[len(prefix):].rstrip('";').split("~")
    if len(parts) < 46:
        raise ValueError(f"Insufficient Tencent data for {symbol}")

    name = parts[1]
    price = float(parts[3])
    last_close = float(parts[4])
    pe = float(parts[39]) if parts[39] else None
    volume = float(parts[36]) if parts[36] else 0

    chg = round((price - last_close) / last_close * 100, 2) if last_close else 0
    trend = "up" if chg > 0 else "down" if chg < 0 else "flat"

    # 52-week high/low computed from 1 year of kline history (quote fields
    # 44/45 are market-cap figures on A-shares, not prices)
    hist_url = (
        f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={code},day,,,260,qfq"
    )
    hr = requests.get(hist_url, timeout=10).json()
    code_data = hr.get("data", {}).get(code, {})
    key = "qfqday" if "qfqday" in code_data else "day"
    days = code_data.get(key, [])
    all_closes = [float(day[2]) for day in days if len(day) > 2 and day[2]]
    closes = [round(c, 2) for c in all_closes[-5:]] if all_closes else []

    return {
        "symbol": symbol,
        "name": name,
        "price": price,
        "mcap": None,
        "pe": pe,
        "52h": round(max(all_closes), 2) if all_closes else None,
        "52l": round(min(all_closes), 2) if all_closes else None,
        "closes": closes,
        "avg": round(sum(closes) / len(closes), 2) if closes else round((price + last_close) / 2, 2),
        "vol": int(volume / 1_000_000),
        "trend": trend,
        "chg": chg,
    }


def _fetch_yahooquery(symbol: str) -> dict[str, Any]:
    """Fetch stock data via yahooquery (best for US, works for HK/China too)."""
    from yahooquery import Ticker

    t = Ticker(symbol)
    info = t.price
    if not isinstance(info, dict) or symbol not in info:
        raise ValueError(f"No price data for {symbol}")

    data = info[symbol]
    current = data.get("regularMarketPrice") or data.get("currentPrice")
    if current is None:
        raise ValueError(f"No current price for {symbol}")

    hist = t.history(period="1mo")
    if isinstance(hist, dict):
        hist = hist.get(symbol)
    closes: list[float] = []
    if hist is not None and not hist.empty:
        closes = [round(float(x), 2) for x in hist["close"].dropna().tolist()[-5:]]

    market_cap = data.get("marketCap")
    if market_cap:
        market_cap = int(int(market_cap) / 1_000_000_000)

    return {
        "symbol": symbol,
        "name": data.get("longName") or data.get("shortName") or symbol,
        "price": current,
        "mcap": market_cap,
        "pe": data.get("trailingPE"),
        "52h": data.get("fiftyTwoWeekHigh"),
        "52l": data.get("fiftyTwoWeekLow"),
        "closes": closes,
        "avg": round(sum(closes) / len(closes), 2) if closes else None,
        "vol": int(data.get("regularMarketVolume", 0) / 1_000_000) if data.get("regularMarketVolume") else None,
        "trend": "up" if closes and closes[-1] > closes[0] else "down" if closes else "flat",
        "chg": round(((closes[-1] - closes[0]) / closes[0]) * 100, 2) if len(closes) >= 2 else 0,
    }


def _fetch_tencent_history(symbol: str, days: int = 60) -> list[float]:
    """Fetch daily closes from Tencent kline API. Returns a list of closes."""
    code = _to_tencent_code(symbol)
    if not code:
        raise ValueError(f"Cannot convert {symbol} to Tencent code")

    # Kline API requires .OQ suffix for US codes (usAAPL.OQ), unlike the quote API
    kline_code = code + ".OQ" if code.startswith("us") else code

    hist_url = (
        f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={kline_code},day,,,{days},qfq"
    )
    hr = requests.get(hist_url, timeout=10).json()
    code_data = hr.get("data", {}).get(kline_code, {})
    key = "qfqday" if "qfqday" in code_data else "day"
    rows = code_data.get(key, [])
    closes = [float(day[2]) for day in rows if len(day) > 2 and day[2]]
    if not closes:
        raise ValueError(f"No history data for {symbol}")
    return closes


def _format_stock_data(data: dict[str, Any]) -> str:
    """Format fetched stock data as a concise string for the LLM."""
    return (
        f"Symbol: {data['symbol']} | Company: {data['name']}\n"
        f"Price: {data['price']} | Market Cap: {data['mcap']}B | P/E: {data['pe']}\n"
        f"52-Week Range: {data['52l']} - {data['52h']}\n"
        f"Recent 5 closes: {data['closes']}\n"
        f"1-Month Avg: {data['avg']} | Trend: {data['trend']} ({data['chg']}%) | Volume: ~{data['vol']}M"
    )


def fetch_stock(symbol: str) -> dict[str, Any] | None:
    """Fetch stock data from the best available source."""
    symbol = symbol.upper().strip()

    try:
        return _fetch_yahooquery(symbol)
    except Exception:
        pass

    if _to_tencent_code(symbol):
        try:
            return _fetch_tencent(symbol)
        except Exception:
            pass

    return None


def _resolve_via_tencent(query: str) -> str | None:
    """Resolve a company name or code via Tencent smartbox search.

    Format: v_hint="sz~000725~京东方A~jdfa~GP-A^sz~200725~京东方B~..."
    """
    try:
        r = requests.get(
            f"http://smartbox.gtimg.cn/s3/?v=2&q={query}&t=all", timeout=5
        )
        r.encoding = "gbk"
        text = r.text.strip()
        prefix = 'v_hint="'
        if not text.startswith(prefix) or len(text) < len(prefix) + 5:
            return None
        payload = text[len(prefix):].rstrip('";')
        items = payload.split("^")
        if not items:
            return None
        parts = items[0].split("~")
        if len(parts) < 3:
            return None
        market, code = parts[0], parts[1]
        if market == "sz":
            return code + ".SZ"
        if market == "sh":
            return code + ".SS"
        if market == "hk":
            return code.zfill(5) + ".HK"
        if market == "us":
            # smartbox returns us codes like aapl.oq — strip the exchange suffix
            return code.split(".")[0].upper()
        return None
    except Exception:
        return None


def resolve_symbol(query: str) -> str | None:
    """Resolve a free-form query to a symbol.

    Order: direct match → static name map → Tencent smartbox (dynamic).
    """
    q = query.strip()
    upper = q.upper()

    # Direct symbol match
    known = {
        "AAPL", "GOOGL", "MSFT", "NVDA", "TSLA", "META", "AMZN",
        "0700.HK", "9988.HK", "9618.HK", "1810.HK", "3690.HK",
        "600519.SS", "000858.SZ", "300750.SZ", "002594.SZ", "688981.SS",
        "000725.SZ",
    }
    if upper in known:
        return upper

    # China 6-digit codes — expand to a full symbol via smartbox
    if re.match(r"^\d{6}$", q):
        return _resolve_via_tencent(q)

    # Static name map
    name_map = {
        "apple": "AAPL",
        "alphabet": "GOOGL",
        "google": "GOOGL",
        "microsoft": "MSFT",
        "nvidia": "NVDA",
        "tesla": "TSLA",
        "meta": "META",
        "facebook": "META",
        "amazon": "AMZN",
        "苹果": "AAPL",
        "谷歌": "GOOGL",
        "微软": "MSFT",
        "英伟达": "NVDA",
        "特斯拉": "TSLA",
        "亚马逊": "AMZN",
        "tencent": "0700.HK",
        "alibaba": "9988.HK",
        "jd": "9618.HK",
        "xiaomi": "1810.HK",
        "meituan": "3690.HK",
        "腾讯": "0700.HK",
        "阿里巴巴": "9988.HK",
        "阿里": "9988.HK",
        "京东": "9618.HK",
        "小米": "1810.HK",
        "美团": "3690.HK",
        "kweichow moutai": "600519.SS",
        "moutai": "600519.SS",
        "wuliangye": "000858.SZ",
        "catl": "300750.SZ",
        "byd": "002594.SZ",
        "smic": "688981.SS",
        "茅台": "600519.SS",
        "贵州茅台": "600519.SS",
        "五粮液": "000858.SZ",
        "宁德时代": "300750.SZ",
        "宁德": "300750.SZ",
        "比亚迪": "002594.SZ",
        "中芯国际": "688981.SS",
        "中芯": "688981.SS",
        # Common A-shares (longest match wins, e.g. BOE over JD substring)
        "京东方": "000725.SZ",
        "平安银行": "000001.SZ",
        "中国平安": "601318.SS",
        "招商银行": "600036.SS",
        "工商银行": "601398.SS",
        "建设银行": "601939.SS",
        "农业银行": "601288.SS",
        "中国银行": "601988.SS",
        "万科": "000002.SZ",
        "格力电器": "000651.SZ",
        "美的集团": "000333.SZ",
        "海康威视": "002415.SZ",
        "隆基绿能": "601012.SS",
        "阳光电源": "300274.SZ",
        "东方财富": "300059.SZ",
        "中信证券": "600030.SS",
        "紫金矿业": "601899.SS",
        "恒瑞医药": "600276.SS",
        "药明康德": "603259.SS",
        "片仔癀": "600436.SS",
        "顺丰控股": "002352.SZ",
        "长江电力": "600900.SS",
        "泸州老窖": "000568.SZ",
        "山西汾酒": "600809.SS",
        "洋河股份": "002304.SZ",
        "伊利股份": "600887.SS",
        "海天味业": "603288.SS",
        "牧原股份": "002714.SZ",
        "三一重工": "600031.SS",
        "汇川技术": "300124.SZ",
        "北方华创": "002371.SZ",
        "韦尔股份": "603501.SS",
        "兆易创新": "603986.SS",
        "中远海控": "601919.SS",
        "中国石化": "600028.SS",
        "中国石油": "601857.SS",
        "中国神华": "601088.SS",
        "中国移动": "600941.SS",
        "中国电信": "601728.SS",
        "中国联通": "600050.SS",
    }
    lower = q.lower()
    best, best_len = None, 0
    for key, sym in name_map.items():
        if key in lower and len(key) > best_len:
            best, best_len = sym, len(key)
    if best:
        return best

    # Dynamic fallback — handles BOE and any other company name via smartbox
    return _resolve_via_tencent(q)


# ── kitegen tools ────────────────────────────────────────────────────────────

import kitegen as kg


@kg.tool
def lookup_stock(symbol: str) -> str:
    """Look up current stock data by symbol or company name.

    Examples: AAPL, 0700.HK, 600519.SS, Tencent, Moutai (Kweichow Moutai).
    """
    sym = resolve_symbol(symbol)
    if not sym:
        sym = symbol.upper().strip()
    data = fetch_stock(sym)
    if not data:
        return f"Could not fetch data for '{symbol}'. Try a valid symbol like AAPL, 0700.HK, or 600519.SS."
    return _format_stock_data(data)


@kg.tool
def get_fundamentals(symbol: str) -> str:
    """Fetch fundamental data: revenue trend, earnings, margins, growth.

    Use to understand the company's financial health.
    """
    from yahooquery import Ticker

    sym = resolve_symbol(symbol) or symbol.upper().strip()

    # Tencent has PE for A-shares/HK; yahooquery covers most other metrics
    tencent_pe = None
    if _to_tencent_code(sym):
        try:
            tencent_pe = _fetch_tencent(sym).get("pe")
        except Exception:
            pass

    try:
        t = Ticker(sym)
        info = t.price
        data = info.get(sym, {}) if isinstance(info, dict) else {}

        financial = t.financial_data
        fin = financial.get(sym, {}) if isinstance(financial, dict) else {}

        earnings = t.earnings_trend
        trend = earnings.get(sym, {}) if isinstance(earnings, dict) else {}

        pe = data.get("trailingPE") or tencent_pe
        return (
            f"Fundamentals for {sym} ({data.get('longName') or data.get('shortName') or sym}):\n"
            f"Sector: {data.get('sector', 'N/A')} | Industry: {data.get('industry', 'N/A')}\n"
            f"Trailing P/E: {pe} | Forward P/E: {data.get('forwardPE')}\n"
            f"Profit Margin: {fin.get('profitMargins')} | Revenue Growth: {fin.get('revenueGrowth')}\n"
            f"Debt/Equity: {fin.get('debtToEquity')} | ROE: {fin.get('returnOnEquity')}\n"
            f"Earnings trend: {trend}"
        )
    except Exception:
        if tencent_pe is not None:
            return (
                f"Fundamentals for {sym} (limited data):\n"
                f"Trailing P/E: {tencent_pe}\n"
                f"(Full fundamentals unavailable for this market via yahooquery)"
            )
        return (
            f"Fundamentals unavailable for {sym}. "
            f"Use lookup_stock and get_technical_summary instead."
        )


@kg.tool
def calculate_position_size(
    symbol: str,
    entry: float,
    stop: float,
    risk_pct: float = 1.0,
) -> str:
    """Calculate how many shares to add for a new position or add-on.

    Uses the risk-based formula:
        shares = (portfolio equity * risk_pct / 100) / (entry - stop)

    Args:
        symbol: Stock symbol.
        entry: Planned entry price.
        stop: Stop-loss price (must be below entry for a long position).
        risk_pct: Percent of portfolio equity to risk on this trade (default 1%).
    """
    from demo.portfolio import load_portfolio
    from demo.tools import fetch_stock

    if entry <= stop:
        return "Invalid plan: stop must be below entry price for a long position."

    portfolio = load_portfolio("default")
    prices: dict[str, float] = {}
    for s in portfolio.positions:
        data = fetch_stock(s)
        if data and data.get("price"):
            prices[s] = data["price"]
    equity = portfolio.equity(prices)

    risk_amount = equity * (risk_pct / 100.0)
    per_share_risk = entry - stop
    shares = int(risk_amount / per_share_risk)

    return (
        f"Position sizing for {symbol}:\n"
        f"Portfolio equity: {equity:.2f}\n"
        f"Risk budget ({risk_pct}%): {risk_amount:.2f}\n"
        f"Entry: {entry} | Stop: {stop} | Risk/share: {per_share_risk:.2f}\n"
        f"Suggested size: {shares} shares (cost ≈ {shares * entry:.2f})\n"
        f"If stopped out, max loss ≈ {shares * per_share_risk:.2f}"
    )


def _compute_technicals(closes: list[float]) -> dict[str, Any]:
    """Compute MA20, MA50, RSI(14) and trend signal from a closes list."""
    def sma(period: int) -> float:
        if len(closes) < period:
            return 0.0
        return round(sum(closes[-period:]) / period, 2)

    def rsi(period: int = 14) -> float:
        if len(closes) < period + 1:
            return 0.0
        gains, losses = 0.0, 0.0
        for i in range(1, period + 1):
            delta = closes[-i] - closes[-i - 1]
            if delta > 0:
                gains += delta
            else:
                losses -= delta
        avg_gain = gains / period
        avg_loss = losses / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    current = closes[-1]
    ma20 = sma(20)
    ma50 = sma(50)
    rsi14 = rsi(14)

    if ma20 and ma50:
        if current > ma20 > ma50:
            signal = "bullish"
        elif current < ma20 < ma50:
            signal = "bearish"
        else:
            signal = "mixed"
    else:
        signal = "insufficient data"

    return {"current": round(current, 2), "ma20": ma20, "ma50": ma50,
            "rsi14": rsi14, "signal": signal}


@kg.tool
def get_technical_summary(symbol: str) -> str:
    """Compute RSI and moving averages for a stock."""
    sym = resolve_symbol(symbol) or symbol.upper().strip()

    # Try yahooquery history first (works for US + some HK)
    try:
        from yahooquery import Ticker

        t = Ticker(sym)
        hist = t.history(period="3mo")
        if isinstance(hist, dict):
            hist = hist.get(sym)
        if hist is not None and not hist.empty:
            closes = [float(x) for x in hist["close"].dropna().tolist()]
            if len(closes) >= 20:
                tech = _compute_technicals(closes)
                if tech["ma20"]:
                    return (
                        f"Technical summary for {sym}:\n"
                        f"Current: {tech['current']} | 20-day MA: {tech['ma20']} | 50-day MA: {tech['ma50']}\n"
                        f"RSI(14): {tech['rsi14']} (overbought >70, oversold <30)\n"
                        f"Trend signal: {tech['signal']}"
                    )
    except Exception:
        pass

    # Fallback: Tencent kline (A-shares, HK)
    try:
        closes = _fetch_tencent_history(sym, days=60)
        tech = _compute_technicals(closes)
        if tech["ma20"]:
            return (
                f"Technical summary for {sym}:\n"
                f"Current: {tech['current']} | 20-day MA: {tech['ma20']} | 50-day MA: {tech['ma50']}\n"
                f"RSI(14): {tech['rsi14']} (overbought >70, oversold <30)\n"
                f"Trend signal: {tech['signal']}"
            )
    except Exception:
        pass

    return f"Not enough price history to compute technicals for {sym}."
