from flask import Flask, request, jsonify, send_file
import yfinance as yf

app = Flask(__name__)


@app.route("/")
def index():
    return send_file("index.html")


@app.route("/api/quote", methods=["POST"])
def quote():
    data = request.json
    symbols = data.get("symbols", [])
    results = {}

    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol.upper())

            # fast_info is more reliable than info on cloud servers
            fi = ticker.fast_info
            price = fi.get("lastPrice", 0) or fi.get("last_price", 0)
            prev_close = fi.get("previousClose", 0) or fi.get("previous_close", 0)

            # Fallback: get price from recent history if fast_info fails
            if not price:
                hist = ticker.history(period="5d")
                if not hist.empty:
                    price = round(hist["Close"].iloc[-1], 2)
                    if len(hist) >= 2:
                        prev_close = round(hist["Close"].iloc[-2], 2)

            change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0

            # info for metadata (name, sector) - may fail, so wrap separately
            name = symbol.upper()
            sector = ""
            quote_type = ""
            try:
                info = ticker.info
                name = info.get("shortName", symbol.upper())
                sector = info.get("sector", "")
                quote_type = info.get("quoteType", "")
            except Exception:
                pass

            results[symbol.upper()] = {
                "name": name,
                "price": price,
                "previousClose": prev_close,
                "currency": "USD",
                "change": round(change_pct, 2),
                "sector": sector,
                "quoteType": quote_type,
            }
        except Exception:
            results[symbol.upper()] = {"error": f"無法取得 {symbol} 的資料"}

    return jsonify(results)


@app.route("/api/history", methods=["POST"])
def history():
    data = request.json
    symbols = data.get("symbols", [])
    period = data.get("period", "5d")
    interval_map = {"1d": "5m", "5d": "1d", "1mo": "1d", "3mo": "1wk", "6mo": "1wk", "1y": "1mo", "max": "1mo"}
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


if __name__ == "__main__":
    app.run(debug=True, port=5050)
