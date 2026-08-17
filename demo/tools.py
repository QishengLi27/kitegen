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
    """Fetch stock data from the best available source.

    China-first ordering: A-shares (.SS/.SZ) and HK go to Tencent first —
    yahooquery is slow/unreliable for them on this network and is the
    fallback. US symbols try yahooquery first, then Tencent.
    """
    symbol = symbol.upper().strip()
    is_cn = symbol.endswith(".SS") or symbol.endswith(".SZ") or symbol.endswith(".HK")

    if is_cn:
        try:
            return _fetch_tencent(symbol)
        except Exception:
            pass
        try:
            return _fetch_yahooquery(symbol)
        except Exception:
            pass
        return None

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


def _compute_indicators(bars: list[dict[str, float]]) -> dict[str, Any]:
    """Compute the full indicator set from OHLC bars.

    Single source of truth for all indicator math. Bars: [{"close": c, "high": h, "low": l}, ...]
    Returns rsi14, ma20, ma50, macd (line/signal/hist), atr14, bollinger, and a
    simple trend regime ("bullish"/"bearish"/"mixed").
    """
    closes = [b["close"] for b in bars]

    def sma(period: int) -> float:
        if len(closes) < period:
            return 0.0
        return round(sum(closes[-period:]) / period, 2)

    def ema(series: list[float], period: int) -> list[float]:
        """Exponential moving average series."""
        if len(series) < period:
            return []
        k = 2.0 / (period + 1)
        out = [sum(series[:period]) / period]
        for x in series[period:]:
            out.append(x * k + out[-1] * (1 - k))
        return out

    # RSI(14) — simple average gains/losses
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

    # MACD(12, 26, 9)
    macd_line: list[float] = []
    macd_signal: list[float] = []
    hist = 0.0
    macd_state = "flat"
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    if ema12 and ema26:
        n = min(len(ema12), len(ema26))
        macd_line = [ema12[-n + i] - ema26[-n + i] for i in range(n)]
        macd_signal = ema(macd_line, 9)
        if len(macd_signal) >= 2:
            hist = round(macd_line[-1] - macd_signal[-1], 4)
            # Cross detection over the last 3 bars — the -1 bound keeps
            # macd_signal[-i-1] in range for short histories
            for i in range(1, min(3, len(macd_signal) - 1) + 1):
                if macd_line[-i] > macd_signal[-i] and macd_line[-i - 1] <= macd_signal[-i - 1]:
                    macd_state = "bullish_cross"
                    break
                if macd_line[-i] < macd_signal[-i] and macd_line[-i - 1] >= macd_signal[-i - 1]:
                    macd_state = "bearish_cross"
                    break
            else:
                macd_state = "bullish" if hist > 0 else "bearish"

    # ATR(14)
    atr = 0.0
    if len(bars) >= 15:
        trs: list[float] = []
        for i in range(1, len(bars)):
            h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        atr = round(sum(trs[-14:]) / 14, 4)

    # Bollinger(20, 2)
    boll = {"upper": 0.0, "mid": 0.0, "lower": 0.0, "position": "unknown"}
    if len(closes) >= 20:
        mid = sma(20)
        window = closes[-20:]
        std = (sum((c - mid) ** 2 for c in window) / 20) ** 0.5
        boll = {
            "upper": round(mid + 2 * std, 2),
            "mid": round(mid, 2),
            "lower": round(mid - 2 * std, 2),
            "position": (
                "upper" if closes[-1] >= mid + 2 * std else
                "lower" if closes[-1] <= mid - 2 * std else
                "middle"
            ),
        }

    current = closes[-1]
    ma20 = sma(20)
    ma50 = sma(50)
    rsi14 = rsi(14)

    if ma20 and ma50:
        if current > ma20 > ma50:
            trend = "bullish"
        elif current < ma20 < ma50:
            trend = "bearish"
        else:
            trend = "mixed"
    else:
        trend = "insufficient data"

    return {
        "current": round(current, 2),
        "ma20": ma20,
        "ma50": ma50,
        "rsi14": rsi14,
        "macd_line": round(macd_line[-1], 4) if macd_line else 0.0,
        "macd_signal": round(macd_signal[-1], 4) if macd_signal else 0.0,
        "macd_hist": hist,
        "macd_state": macd_state,
        "atr14": atr,
        "bollinger": boll,
        "trend": trend,
    }


def _get_history(symbol: str, days: int = 80) -> list[dict[str, float]] | None:
    """Fetch OHLC daily bars — Tencent-first for A-shares/HK (China-first
    ordering), yahooquery for US, with the other as fallback."""
    sym = resolve_symbol(symbol) or symbol.upper().strip()
    is_cn = sym.endswith(".SS") or sym.endswith(".SZ") or sym.endswith(".HK")

    def _from_yahooquery() -> list[dict[str, float]] | None:
        from yahooquery import Ticker

        t = Ticker(sym)
        hist = t.history(period="6mo")
        if isinstance(hist, dict):
            hist = hist.get(sym)
        if hist is not None and not hist.empty:
            df = hist.dropna(subset=["close"])
            bars = [
                {"close": float(r.close), "high": float(r.high), "low": float(r.low)}
                for r in df.itertuples()
            ]
            if len(bars) >= 20:
                return bars[-days:]
        return None

    def _from_tencent() -> list[dict[str, float]] | None:
        code = _to_tencent_code(sym)
        if not code:
            return None
        kline_code = code + ".OQ" if code.startswith("us") else code
        hr = requests.get(
            f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param={kline_code},day,,,{days},qfq",
            timeout=10,
        ).json()
        code_data = hr.get("data", {}).get(kline_code, {})
        key = "qfqday" if "qfqday" in code_data else "day"
        rows = code_data.get(key, [])
        bars = [
            # row format: [date, open, close, high, low, volume]
            {"close": float(r[2]), "high": float(r[3]), "low": float(r[4])}
            for r in rows if len(r) > 4 and r[2]
        ]
        return bars if len(bars) >= 20 else None

    primary, fallback = (_from_tencent, _from_yahooquery) if is_cn else (_from_yahooquery, _from_tencent)
    for fetcher in (primary, fallback):
        try:
            bars = fetcher()
            if bars:
                return bars
        except Exception:
            pass

    return None


def _compute_technicals(closes: list[float]) -> dict[str, Any]:
    """Backward-compat wrapper: full indicators from a closes list (no OHLC)."""
    bars = [{"close": c, "high": c, "low": c} for c in closes]
    return _compute_indicators(bars)


@kg.tool
def get_technical_summary(symbol: str) -> str:
    """Compute RSI, moving averages, MACD, ATR, and Bollinger for a stock."""
    sym = resolve_symbol(symbol) or symbol.upper().strip()
    bars = _get_history(sym)
    if not bars:
        return f"Not enough price history to compute technicals for {sym}."

    tech = _compute_indicators(bars)
    if not tech["ma20"]:
        return f"Not enough price history to compute technicals for {sym}."

    return (
        f"Technical summary for {sym}:\n"
        f"Current: {tech['current']} | 20-day MA: {tech['ma20']} | 50-day MA: {tech['ma50']}\n"
        f"RSI(14): {tech['rsi14']} (overbought >70, oversold <30)\n"
        f"MACD: line {tech['macd_line']} vs signal {tech['macd_signal']} ({tech['macd_state']})\n"
        f"ATR(14): {tech['atr14']} | Bollinger: {tech['bollinger']['upper']}/{tech['bollinger']['mid']}/{tech['bollinger']['lower']} ({tech['bollinger']['position']})\n"
        f"Trend regime: {tech['trend']}"
    )


@kg.tool
def compute_signal(symbol: str) -> str:
    """Compute a composite technical signal for a stock.

    Deterministic vote-based regime classification — no ML:
      - Trend vote:     price vs MA20 vs MA50 alignment
      - Momentum vote:  RSI zone (55/45 thresholds)
      - MACD vote:      line vs signal, fresh crosses prioritized

    Signal = strong_bullish / bullish / neutral / bearish / strong_bearish.
    Confidence = fraction of agreeing votes (1.0 = all three agree).

    Also returns ATR-based key levels: support, resistance, stop-loss,
    and two take-profit targets. Use this whenever giving short-term
    trading advice — base stop-loss/take-profit levels on these numbers.
    """
    sym = resolve_symbol(symbol) or symbol.upper().strip()
    bars = _get_history(sym)
    if not bars:
        return f"Not enough price history to compute a signal for {sym}."

    tech = _compute_indicators(bars)
    if not tech["ma20"] or not tech["atr14"]:
        return f"Not enough price history to compute a signal for {sym}."

    # ── Votes (deterministic, explainable) ────────────────────────────────
    votes: list[tuple[str, int]] = []

    trend_vote = 0
    if tech["current"] > tech["ma20"] > tech["ma50"]:
        trend_vote = 1
    elif tech["current"] < tech["ma20"] < tech["ma50"]:
        trend_vote = -1
    votes.append(("trend", trend_vote))

    rsi = tech["rsi14"]
    momentum_vote = 1 if rsi > 55 else -1 if rsi < 45 else 0
    votes.append(("momentum", momentum_vote))

    macd_state = tech["macd_state"]
    macd_vote = 1 if macd_state in ("bullish", "bullish_cross") else \
                -1 if macd_state in ("bearish", "bearish_cross") else 0
    votes.append(("macd", macd_vote))

    total = sum(v for _, v in votes)
    # "strong" requires all three votes aligned — two votes with a mixed
    # third is just bullish/bearish
    signal = (
        "strong_bullish" if total == 3 else
        "bullish" if total >= 1 else
        "neutral" if total == 0 else
        "bearish" if total <= -1 else
        "strong_bearish"
    )
    confidence = round(abs(total) / len(votes), 2)

    # ── Volatility regime (ATR relative to recent average) ────────────────
    atrs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        atrs.append(max(h - l, abs(h - pc), abs(l - pc)))
    avg_atr = sum(atrs[-30:]) / len(atrs[-30:]) if atrs else 0
    atr_ratio = tech["atr14"] / avg_atr if avg_atr else 1.0
    volatility = "high" if atr_ratio > 1.5 else "low" if atr_ratio < 0.7 else "normal"

    # ── Key levels from recent price action + ATR ─────────────────────────
    lows = [b["low"] for b in bars[-20:]]
    highs = [b["high"] for b in bars[-20:]]
    support = round(min(lows), 2)
    resistance = round(max(highs), 2)
    atr = tech["atr14"]

    levels = {
        "support": support,
        "resistance": resistance,
        "stop_loss": round(support - atr, 2),
        "take_profit_1": round(tech["current"] + 2 * atr, 2),
        "take_profit_2": round(resistance + atr, 2),
    }

    reasons = [name for name, v in votes if v != 0]
    summary = (
        f"{sym} composite signal: {signal} (confidence {confidence}). "
        f"Trend {tech['trend']}, RSI {rsi} ({'bullish' if momentum_vote > 0 else 'bearish' if momentum_vote < 0 else 'neutral'}), "
        f"MACD {macd_state}, volatility {volatility}. "
        f"Votes from: {', '.join(reasons) or 'none — mixed signals'}."
    )

    return (
        f"Signal: {signal}\n"
        f"Confidence: {confidence} (agreement among trend/momentum/MACD votes)\n"
        f"{summary}\n\n"
        f"Indicators:\n"
        f"- Current: {tech['current']} | MA20: {tech['ma20']} | MA50: {tech['ma50']}\n"
        f"- RSI(14): {rsi}\n"
        f"- MACD: line {tech['macd_line']} vs signal {tech['macd_signal']} ({macd_state})\n"
        f"- ATR(14): {atr} | Volatility: {volatility}\n"
        f"- Bollinger: {tech['bollinger']['upper']}/{tech['bollinger']['mid']}/{tech['bollinger']['lower']} ({tech['bollinger']['position']})\n\n"
        f"Key levels (ATR-based):\n"
        f"- Support: {levels['support']}\n"
        f"- Resistance: {levels['resistance']}\n"
        f"- Suggested stop-loss: {levels['stop_loss']}\n"
        f"- Take-profit 1: {levels['take_profit_1']} | Take-profit 2: {levels['take_profit_2']}"
    )
