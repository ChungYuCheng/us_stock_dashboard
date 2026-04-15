import os
import logging

import requests as http_requests
import yfinance as yf
from flask import Flask, request, jsonify, send_file

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("stock-api")

# API keys from environment variables (set in Render dashboard)
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
TWELVE_KEY = os.environ.get("TWELVE_DATA_API_KEY", "")
ALPHA_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")


# ── Quote providers (fallback chain) ──────────────────────────

def quote_yfinance(symbol):
    """Provider 1: yfinance (no API key needed)"""
    ticker = yf.Ticker(symbol)
    fi = ticker.fast_info
    price = fi.get("lastPrice", 0) or fi.get("last_price", 0)
    prev_close = fi.get("previousClose", 0) or fi.get("previous_close", 0)

    if not price:
        hist = ticker.history(period="5d")
        if not hist.empty:
            price = round(hist["Close"].iloc[-1], 2)
            if len(hist) >= 2:
                prev_close = round(hist["Close"].iloc[-2], 2)

    if not price:
        return None

    name, sector, quote_type = symbol, "", ""
    try:
        info = ticker.info
        name = info.get("shortName", symbol)
        sector = info.get("sector", "")
        quote_type = info.get("quoteType", "")
    except Exception:
        pass

    return _build_quote(symbol, price, prev_close, name, sector, quote_type, "yfinance")


def quote_finnhub(symbol):
    """Provider 2: Finnhub (60 req/min free)"""
    if not FINNHUB_KEY:
        return None
    resp = http_requests.get(
        f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={FINNHUB_KEY}",
        timeout=8,
    )
    data = resp.json()
    price = data.get("c", 0)
    if not price:
        return None
    prev_close = data.get("pc", 0)

    # Finnhub profile for name/sector
    name, sector = symbol, ""
    try:
        p = http_requests.get(
            f"https://finnhub.io/api/v1/stock/profile2?symbol={symbol}&token={FINNHUB_KEY}",
            timeout=5,
        ).json()
        name = p.get("name", symbol)
        sector = p.get("finnhubIndustry", "")
    except Exception:
        pass

    return _build_quote(symbol, price, prev_close, name, sector, "", "finnhub")


def quote_twelvedata(symbol):
    """Provider 3: Twelve Data (800 req/day free)"""
    if not TWELVE_KEY:
        return None
    resp = http_requests.get(
        f"https://api.twelvedata.com/quote?symbol={symbol}&apikey={TWELVE_KEY}",
        timeout=8,
    )
    data = resp.json()
    if data.get("code") or data.get("status") == "error":
        return None
    price = float(data.get("close", 0))
    if not price:
        return None
    prev_close = float(data.get("previous_close", 0))
    name = data.get("name", symbol)

    return _build_quote(symbol, price, prev_close, name, "", "", "twelvedata")


def quote_alphavantage(symbol):
    """Provider 4: Alpha Vantage (25 req/day free)"""
    if not ALPHA_KEY:
        return None
    resp = http_requests.get(
        f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={ALPHA_KEY}",
        timeout=8,
    )
    data = resp.json().get("Global Quote", {})
    price = float(data.get("05. price", 0))
    if not price:
        return None
    prev_close = float(data.get("08. previous close", 0))

    return _build_quote(symbol, price, prev_close, symbol, "", "", "alphavantage")


def _build_quote(symbol, price, prev_close, name, sector, quote_type, source):
    change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0
    return {
        "name": name,
        "price": price,
        "previousClose": prev_close,
        "currency": "USD",
        "change": round(change_pct, 2),
        "sector": sector,
        "quoteType": quote_type,
        "source": source,
    }


# Ordered fallback chain
PROVIDERS = [
    ("yfinance", quote_yfinance),
    ("finnhub", quote_finnhub),
    ("twelvedata", quote_twelvedata),
    ("alphavantage", quote_alphavantage),
]


def get_quote(symbol):
    """Try each provider in order, return first success."""
    for name, fn in PROVIDERS:
        try:
            result = fn(symbol)
            if result and result.get("price"):
                log.info(f"{symbol}: got quote from {name}")
                return result
        except Exception as e:
            log.warning(f"{symbol}: {name} failed — {e}")
    return {"error": f"所有報價來源皆失敗：{symbol}"}


# ── Routes ────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_file("index.html")


@app.route("/api/quote", methods=["POST"])
def quote():
    data = request.json
    symbols = data.get("symbols", [])
    results = {}
    for symbol in symbols:
        results[symbol.upper()] = get_quote(symbol.upper())
    return jsonify(results)


@app.route("/api/history", methods=["POST"])
def history():
    data = request.json
    symbols = data.get("symbols", [])
    period = data.get("period", "5d")
    interval_map = {
        "1d": "5m", "5d": "1d", "1mo": "1d",
        "3mo": "1wk", "6mo": "1wk", "1y": "1mo", "max": "1mo",
    }
    interval = interval_map.get(period, "1d")
    results = {}

    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol.upper())
            hist = ticker.history(period=period, interval=interval)
            if hist.empty:
                results[symbol.upper()] = {"error": "無歷史資料"}
                continue

            dates = []
            for ts in hist.index:
                if period == "1d":
                    dates.append(ts.strftime("%H:%M"))
                elif period in ("1y", "max"):
                    dates.append(ts.strftime("%Y/%m"))
                else:
                    dates.append(ts.strftime("%m/%d"))

            results[symbol.upper()] = {
                "dates": dates,
                "prices": [round(p, 2) for p in hist["Close"].tolist()],
            }
        except Exception as e:
            results[symbol.upper()] = {"error": str(e)}

    return jsonify(results)


@app.route("/api/sources")
def sources():
    """Check which providers are configured."""
    return jsonify({
        "yfinance": True,
        "finnhub": bool(FINNHUB_KEY),
        "twelvedata": bool(TWELVE_KEY),
        "alphavantage": bool(ALPHA_KEY),
    })


if __name__ == "__main__":
    app.run(debug=True, port=5050)
