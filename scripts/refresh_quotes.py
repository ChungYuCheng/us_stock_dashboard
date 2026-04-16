"""
Fetch stock quotes and push cache to GitHub.
Runs in GitHub Actions, NOT on Render.
"""

import os
import sys
import json
import time
import base64

import requests
import yfinance as yf

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ.get("GITHUB_REPO", "ChungYuCheng/us_stock_dashboard")
ALPHA_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
CACHE_PATH = "cache_data.json"


def is_tw_stock(symbol):
    s = symbol.upper()
    return s.endswith(".TW") or s.endswith(".TWO")


def github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


def load_cache_from_github():
    """Pull existing cache from GitHub."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{CACHE_PATH}"
    resp = requests.get(url, headers=github_headers(), timeout=10)
    if resp.status_code == 200:
        data = resp.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return json.loads(content), data["sha"]
    return {}, None


def save_cache_to_github(cache_dict, sha=None):
    """Push cache to GitHub."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{CACHE_PATH}"
    content = base64.b64encode(
        json.dumps(cache_dict, ensure_ascii=False, indent=2).encode("utf-8")
    ).decode("utf-8")

    payload = {
        "message": f"Daily refresh: {len(cache_dict.get('quotes', {}))} quotes updated",
        "content": content,
        "branch": "main",
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(url, headers=github_headers(), json=payload, timeout=15)
    if resp.status_code in (200, 201):
        print("Cache saved to GitHub successfully")
        return True
    else:
        print(f"GitHub save FAILED: {resp.status_code} {resp.text[:300]}")
        return False


def fetch_yfinance(symbol):
    """Fetch quote via yfinance."""
    try:
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

        return build_quote(symbol, price, prev_close, name, sector, quote_type, "yfinance")
    except Exception as e:
        print(f"  yfinance failed: {e}")
        return None


def fetch_alphavantage(symbol):
    """Fetch quote via Alpha Vantage."""
    if not ALPHA_KEY:
        return None
    try:
        resp = requests.get(
            f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={ALPHA_KEY}",
            timeout=10,
        )
        data = resp.json().get("Global Quote", {})
        price = float(data.get("05. price", 0))
        if not price:
            return None
        prev_close = float(data.get("08. previous close", 0))
        return build_quote(symbol, price, prev_close, symbol, "", "", "alphavantage")
    except Exception as e:
        print(f"  alphavantage failed: {e}")
        return None


def build_quote(symbol, price, prev_close, name, sector, quote_type, source):
    change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0
    currency = "TWD" if is_tw_stock(symbol) else "USD"
    return {
        "name": name,
        "price": price,
        "previousClose": prev_close,
        "currency": currency,
        "change": round(change_pct, 2),
        "sector": sector,
        "quoteType": quote_type,
        "source": source,
    }


def main():
    print("Loading existing cache from GitHub...")
    cache, sha = load_cache_from_github()
    symbols = cache.get("symbols", [])

    if not symbols:
        print("No symbols to refresh. Exiting.")
        sys.exit(0)

    print(f"Refreshing {len(symbols)} symbols: {', '.join(symbols)}")

    quotes = cache.get("quotes", {})
    success, failed = [], []

    for sym in symbols:
        print(f"\n[{sym}]")

        # Try yfinance first
        result = fetch_yfinance(sym)
        if result:
            prefix = "NT$" if is_tw_stock(sym) else "$"
            print(f"  OK via yfinance: {prefix}{result['price']:.2f}")
        else:
            # Fallback to Alpha Vantage (US stocks only)
            if not is_tw_stock(sym):
                print(f"  yfinance failed, trying Alpha Vantage...")
                result = fetch_alphavantage(sym)
                if result:
                    print(f"  OK via alphavantage: ${result['price']:.2f}")
            else:
                print(f"  yfinance failed (no fallback for TW stocks)")

        if result:
            quotes[sym] = {"data": result, "ts": time.time()}
            success.append(sym)
        else:
            print(f"  ALL SOURCES FAILED")
            failed.append(sym)

        time.sleep(1)  # rate limit buffer

    # Update cache
    cache["quotes"] = quotes
    cache["symbols"] = symbols
    cache["last_refresh"] = time.time()

    # Pre-fetch history for all symbols (5d + 1mo)
    print(f"\nFetching history data...")
    history = {}
    periods = [
        ("1d", "5m", "%H:%M"),
        ("5d", "1d", "%m/%d"),
        ("1mo", "1d", "%m/%d"),
    ]
    for sym in symbols:
        for period, interval, date_fmt in periods:
            try:
                ticker = yf.Ticker(sym)
                hist = ticker.history(period=period, interval=interval)
                if not hist.empty:
                    dates = [ts.strftime(date_fmt) for ts in hist.index]
                    prices = [round(p, 2) for p in hist["Close"].tolist()]
                    history[f"{sym}:{period}"] = {"data": {"dates": dates, "prices": prices}, "ts": time.time()}
                    print(f"  {sym}:{period} — {len(prices)} points")
                time.sleep(0.3)
            except Exception as e:
                print(f"  {sym}:{period} — failed: {e}")

    cache["history"] = history

    print(f"\n{'='*40}")
    print(f"Success: {len(success)} | Failed: {len(failed)}")
    if failed:
        print(f"Failed symbols: {', '.join(failed)}")

    print("\nSaving cache to GitHub...")
    ok = save_cache_to_github(cache, sha)

    if not ok or failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
