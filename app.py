"""
Stock Dashboard Backend — Cache-only mode.
Render NEVER calls yfinance or any external API.
All quote fetching happens in GitHub Actions or locally.
"""

import os
import json
import time
import base64
import logging
import threading

import requests as http_requests
from flask import Flask, request, jsonify, send_file

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("stock-api")

# GitHub cache storage
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "ChungYuCheng/us_stock_dashboard")
GITHUB_CACHE_PATH = "cache_data.json"

QUOTE_CACHE_TTL = int(os.environ.get("QUOTE_CACHE_TTL", "86400"))  # 24h
HISTORY_CACHE_TTL = int(os.environ.get("HISTORY_CACHE_TTL", "86400"))  # 24h


def is_tw_stock(symbol):
    s = symbol.upper()
    return s.endswith(".TW") or s.endswith(".TWO")


# ── GitHub cache read/write ──────────────────────────────────

def github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


def load_cache_from_github():
    if not GITHUB_TOKEN:
        log.warning("No GITHUB_TOKEN set")
        return None
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_CACHE_PATH}"
        resp = http_requests.get(url, headers=github_headers(), timeout=10)
        if resp.status_code == 200:
            content = base64.b64decode(resp.json()["content"]).decode("utf-8")
            data = json.loads(content)
            log.info(f"GitHub cache loaded: {len(data.get('quotes', {}))} quotes, {len(data.get('symbols', []))} symbols")
            return data
        elif resp.status_code == 404:
            log.info("No cache on GitHub yet")
        else:
            log.warning(f"GitHub load failed: {resp.status_code}")
    except Exception as e:
        log.warning(f"GitHub cache load error: {e}")
    return None


def save_symbols_to_github(symbols_list):
    """Update only the symbols list in GitHub cache (doesn't touch quotes)."""
    if not GITHUB_TOKEN:
        return False
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_CACHE_PATH}"

        # Load existing cache
        resp = http_requests.get(url, headers=github_headers(), timeout=10)
        if resp.status_code == 200:
            existing = json.loads(base64.b64decode(resp.json()["content"]).decode("utf-8"))
            sha = resp.json()["sha"]
        else:
            existing = {}
            sha = None

        # Merge symbols
        old_symbols = set(existing.get("symbols", []))
        new_symbols = set(symbols_list)
        merged = sorted(old_symbols | new_symbols)

        if set(merged) == old_symbols:
            return True  # No change needed

        existing["symbols"] = merged
        content = base64.b64encode(json.dumps(existing, ensure_ascii=False).encode("utf-8")).decode("utf-8")

        payload = {
            "message": f"Track new symbols: {sorted(new_symbols - old_symbols)}",
            "content": content,
            "branch": "main",
        }
        if sha:
            payload["sha"] = sha

        resp = http_requests.put(url, headers=github_headers(), json=payload, timeout=15)
        if resp.status_code in (200, 201):
            log.info(f"Symbols updated on GitHub: {merged}")
            return True
        else:
            log.warning(f"GitHub symbols save failed: {resp.status_code}")
    except Exception as e:
        log.warning(f"GitHub symbols save error: {e}")
    return False


# ── Read-only cache ──────────────────────────────────────────

class ReadOnlyCache:
    def __init__(self):
        self._quotes = {}
        self._history = {}
        self._symbols = set()
        self._lock = threading.Lock()
        self._last_refresh = 0
        self._load()

    def _load(self):
        saved = load_cache_from_github()
        if saved:
            self._apply(saved)
        else:
            log.info("No cache available, waiting for GitHub Actions refresh")

    def _apply(self, saved):
        with self._lock:
            self._quotes = saved.get("quotes", {})
            self._history = saved.get("history", {})
            self._symbols = set(saved.get("symbols", []))
            self._last_refresh = saved.get("last_refresh", 0)

    def reload_from_github(self):
        saved = load_cache_from_github()
        if saved:
            self._apply(saved)
            log.info(f"Reloaded: {len(self._quotes)} quotes, {len(self._history)} history entries")
            return True
        return False

    def track_symbols(self, symbols):
        with self._lock:
            new = set(s.upper() for s in symbols) - self._symbols
            if new:
                self._symbols.update(new)
                log.info(f"New symbols tracked: {new}")
                # Push updated list to GitHub in background
                threading.Thread(
                    target=save_symbols_to_github,
                    args=(list(self._symbols),),
                    daemon=True,
                ).start()

    def get_quote(self, symbol):
        with self._lock:
            entry = self._quotes.get(symbol)
            if entry and (time.time() - entry["ts"]) < QUOTE_CACHE_TTL:
                data = entry["data"].copy()
                data["source"] = data.get("source", "?") + " (cached)"
                return data
        return None

    def get_history(self, key):
        with self._lock:
            entry = self._history.get(key)
            if entry and (time.time() - entry["ts"]) < HISTORY_CACHE_TTL:
                return entry["data"]
        return None

    def stats(self):
        with self._lock:
            ago = f"{int(time.time() - self._last_refresh)}s" if self._last_refresh else "never"
            return {
                "tracked_symbols": sorted(self._symbols),
                "cached_quotes": len(self._quotes),
                "cached_history": len(self._history),
                "last_refresh": self._last_refresh,
                "last_refresh_ago": ago,
                "github_enabled": bool(GITHUB_TOKEN),
            }


cache = ReadOnlyCache()


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
    uncached = []
    for symbol in symbols:
        q = cache.get_quote(symbol)
        if q:
            results[symbol] = q
        else:
            uncached.append(symbol)
            results[symbol] = {"error": "尚無快取資料，請等待下次報價更新"}

    if uncached:
        log.info(f"Uncached symbols: {uncached} — added to tracking, await next refresh")

    return jsonify(results)


@app.route("/api/history", methods=["POST"])
def history():
    data = request.json
    symbols = [s.upper() for s in data.get("symbols", [])]
    period = data.get("period", "5d")
    results = {}

    for sym in symbols:
        cache_key = f"{sym}:{period}"
        cached = cache.get_history(cache_key)
        if cached:
            results[sym] = cached
        else:
            results[sym] = {"error": "無歷史快取資料"}

    return jsonify(results)


@app.route("/api/reload-cache", methods=["POST"])
def reload_cache():
    ok = cache.reload_from_github()
    return jsonify({"reloaded": ok, "cache": cache.stats()})


@app.route("/api/debug-github")
def debug_github():
    """Debug endpoint to check GitHub API connectivity."""
    token_preview = GITHUB_TOKEN[:8] + "..." if GITHUB_TOKEN else "(empty)"
    result = {"token_set": bool(GITHUB_TOKEN), "token_preview": token_preview, "repo": GITHUB_REPO}
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_CACHE_PATH}"
        resp = http_requests.get(url, headers=github_headers(), timeout=10)
        result["status_code"] = resp.status_code
        if resp.status_code == 200:
            result["file_size"] = resp.json().get("size", 0)
            result["ok"] = True
        else:
            result["error"] = resp.text[:300]
            result["ok"] = False
    except Exception as e:
        result["error"] = str(e)
        result["ok"] = False
    return jsonify(result)


@app.route("/api/sources")
def sources():
    return jsonify({
        "mode": "cache-only (no external API calls)",
        "cache": cache.stats(),
        "config": {
            "quote_cache_ttl": QUOTE_CACHE_TTL,
            "history_cache_ttl": HISTORY_CACHE_TTL,
        },
    })


if __name__ == "__main__":
    app.run(debug=True, port=5050)
