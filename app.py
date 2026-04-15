import os
import json
import time
import base64
import logging
import threading

import requests as http_requests
import yfinance as yf
from flask import Flask, request, jsonify, send_file

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("stock-api")

# API keys
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
TWELVE_KEY = os.environ.get("TWELVE_DATA_API_KEY", "")
ALPHA_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")

# GitHub cache storage
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "ChungYuCheng/us_stock_dashboard")
GITHUB_CACHE_PATH = "cache_data.json"

# Cache TTLs
QUOTE_CACHE_TTL = int(os.environ.get("QUOTE_CACHE_TTL", "86400"))  # 24h
HISTORY_CACHE_TTL = int(os.environ.get("HISTORY_CACHE_TTL", "21600"))  # 6h

# Daily refresh hour in UTC (default 21:00 UTC = US market close + 1h)
REFRESH_HOUR_UTC = int(os.environ.get("REFRESH_HOUR_UTC", "21"))


# ── GitHub cache storage ─────────────────────────────────────

def github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


def load_cache_from_github():
    """Pull cache_data.json from GitHub repo."""
    if not GITHUB_TOKEN:
        log.warning("No GITHUB_TOKEN, skipping GitHub cache load")
        return None
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_CACHE_PATH}"
        resp = http_requests.get(url, headers=github_headers(), timeout=10)
        if resp.status_code == 200:
            content = base64.b64decode(resp.json()["content"]).decode("utf-8")
            data = json.loads(content)
            log.info(f"Cache loaded from GitHub: {len(data.get('quotes', {}))} quotes, {len(data.get('symbols', []))} symbols")
            return data
        elif resp.status_code == 404:
            log.info("No cache file on GitHub yet")
        else:
            log.warning(f"GitHub load failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        log.warning(f"GitHub cache load error: {e}")
    return None


def save_cache_to_github(data_dict):
    """Push cache_data.json to GitHub repo."""
    if not GITHUB_TOKEN:
        log.warning("No GITHUB_TOKEN, skipping GitHub cache save")
        return False
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_CACHE_PATH}"
        content = base64.b64encode(json.dumps(data_dict, ensure_ascii=False).encode("utf-8")).decode("utf-8")

        # Get existing file SHA (needed for update)
        sha = None
        resp = http_requests.get(url, headers=github_headers(), timeout=10)
        if resp.status_code == 200:
            sha = resp.json()["sha"]

        payload = {
            "message": f"Update cache: {len(data_dict.get('quotes', {}))} quotes",
            "content": content,
            "branch": "main",
        }
        if sha:
            payload["sha"] = sha

        resp = http_requests.put(url, headers=github_headers(), json=payload, timeout=15)
        if resp.status_code in (200, 201):
            log.info("Cache saved to GitHub successfully")
            return True
        else:
            log.warning(f"GitHub save failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        log.warning(f"GitHub cache save error: {e}")
    return False


# ── Persistent cache ─────────────────────────────────────────

class PersistentCache:
    def __init__(self):
        self._quotes = {}
        self._history = {}
        self._symbols = set()
        self._lock = threading.Lock()
        self._last_refresh = 0
        self._load()

    def _load(self):
        # Try GitHub first, then local file
        saved = load_cache_from_github()
        if not saved:
            try:
                with open(os.path.join(os.path.dirname(__file__), "cache_data.json"), "r") as f:
                    saved = json.load(f)
                log.info("Cache loaded from local file")
            except (FileNotFoundError, json.JSONDecodeError):
                log.info("No cache found, starting fresh")
                return

        if saved:
            self._quotes = saved.get("quotes", {})
            self._history = saved.get("history", {})
            self._symbols = set(saved.get("symbols", []))
            self._last_refresh = saved.get("last_refresh", 0)

    def _to_dict(self):
        return {
            "quotes": self._quotes,
            "history": self._history,
            "symbols": list(self._symbols),
            "last_refresh": self._last_refresh,
        }

    def save_to_github(self):
        with self._lock:
            return save_cache_to_github(self._to_dict())

    def track_symbols(self, symbols):
        with self._lock:
            new = set(s.upper() for s in symbols) - self._symbols
            if new:
                self._symbols.update(new)
                log.info(f"Tracking new symbols: {new}")

    def get_all_symbols(self):
        with self._lock:
            return list(self._symbols)

    def get_quote(self, symbol):
        with self._lock:
            entry = self._quotes.get(symbol)
            if entry and (time.time() - entry["ts"]) < QUOTE_CACHE_TTL:
                data = entry["data"].copy()
                data["source"] = data.get("source", "?") + " (cached)"
                return data
        return None

    def set_quote(self, symbol, data):
        with self._lock:
            self._quotes[symbol] = {"data": data, "ts": time.time()}

    def get_history(self, key):
        with self._lock:
            entry = self._history.get(key)
            if entry and (time.time() - entry["ts"]) < HISTORY_CACHE_TTL:
                return entry["data"]
        return None

    def set_history(self, key, data):
        with self._lock:
            self._history[key] = {"data": data, "ts": time.time()}

    def set_last_refresh(self):
        with self._lock:
            self._last_refresh = time.time()

    def stats(self):
        with self._lock:
            return {
                "tracked_symbols": len(self._symbols),
                "cached_quotes": len(self._quotes),
                "cached_history": len(self._history),
                "last_refresh": self._last_refresh,
                "last_refresh_ago": f"{int(time.time() - self._last_refresh)}s" if self._last_refresh else "never",
                "github_enabled": bool(GITHUB_TOKEN),
            }


cache = PersistentCache()


# ── Quote providers ───────────────────────────────────────────

def quote_yfinance(symbol):
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


PROVIDERS = [
    ("yfinance", quote_yfinance),
    ("finnhub", quote_finnhub),
    ("twelvedata", quote_twelvedata),
    ("alphavantage", quote_alphavantage),
]


def fetch_quote_fresh(symbol):
    for name, fn in PROVIDERS:
        try:
            result = fn(symbol)
            if result and result.get("price"):
                log.info(f"{symbol}: got quote from {name}")
                cache.set_quote(symbol, result)
                return result
        except Exception as e:
            log.warning(f"{symbol}: {name} failed — {e}")
    return {"error": f"所有報價來源皆失敗：{symbol}"}


def get_quote(symbol):
    cached = cache.get_quote(symbol)
    if cached:
        log.info(f"{symbol}: serving from cache")
        return cached
    return fetch_quote_fresh(symbol)


# ── Daily batch refresh ──────────────────────────────────────

def refresh_all_symbols():
    symbols = cache.get_all_symbols()
    if not symbols:
        log.info("Daily refresh: no symbols to refresh")
        return {"refreshed": 0, "failed": [], "symbols": [], "github_saved": False}

    log.info(f"Daily refresh: updating {len(symbols)} symbols...")
    success, failed = [], []

    for sym in symbols:
        result = fetch_quote_fresh(sym)
        if result.get("error"):
            failed.append(sym)
        else:
            success.append(sym)
        time.sleep(0.5)

    cache.set_last_refresh()

    # Save to GitHub after refresh
    github_ok = cache.save_to_github()

    log.info(f"Daily refresh done: {len(success)} ok, {len(failed)} failed, GitHub: {github_ok}")
    return {
        "refreshed": len(success),
        "failed": failed,
        "symbols": success,
        "github_saved": github_ok,
    }


def _daily_scheduler():
    import datetime
    last_date = None

    while True:
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            today = now.date()

            if now.hour >= REFRESH_HOUR_UTC and last_date != today:
                log.info("Triggering scheduled daily refresh")
                refresh_all_symbols()
                last_date = today

        except Exception as e:
            log.error(f"Scheduler error: {e}")

        time.sleep(600)


_scheduler_thread = threading.Thread(target=_daily_scheduler, daemon=True)
_scheduler_thread.start()


# ── Routes ────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_file("index.html")


@app.route("/api/quote", methods=["POST"])
def quote():
    data = request.json
    symbols = [s.upper() for s in data.get("symbols", [])]
    cache.track_symbols(symbols)

    results = {}
    for symbol in symbols:
        results[symbol] = get_quote(symbol)
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
        sym = symbol.upper()
        cache_key = f"{sym}:{period}"

        cached = cache.get_history(cache_key)
        if cached:
            results[sym] = cached
            continue

        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period=period, interval=interval)
            if hist.empty:
                results[sym] = {"error": "無歷史資料"}
                continue

            dates = []
            for ts in hist.index:
                if period == "1d":
                    dates.append(ts.strftime("%H:%M"))
                elif period in ("1y", "max"):
                    dates.append(ts.strftime("%Y/%m"))
                else:
                    dates.append(ts.strftime("%m/%d"))

            entry = {
                "dates": dates,
                "prices": [round(p, 2) for p in hist["Close"].tolist()],
            }
            cache.set_history(cache_key, entry)
            results[sym] = entry
        except Exception as e:
            results[sym] = {"error": str(e)}

    return jsonify(results)


@app.route("/api/refresh", methods=["POST"])
def manual_refresh():
    result = refresh_all_symbols()
    return jsonify(result)


@app.route("/api/sources")
def sources():
    return jsonify({
        "providers": {
            "yfinance": True,
            "finnhub": bool(FINNHUB_KEY),
            "twelvedata": bool(TWELVE_KEY),
            "alphavantage": bool(ALPHA_KEY),
        },
        "cache": cache.stats(),
        "config": {
            "quote_cache_ttl": QUOTE_CACHE_TTL,
            "history_cache_ttl": HISTORY_CACHE_TTL,
            "refresh_hour_utc": REFRESH_HOUR_UTC,
        },
    })


if __name__ == "__main__":
    app.run(debug=True, port=5050)
