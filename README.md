# 美股台股儀表板 — 專案總覽

## 基本資訊

- **專案名稱**：美股台股儀表板
- **網址**：https://stock.cyclinebot.uk
- **GitHub Repo**：`ChungYuCheng/us_stock_dashboard`（Private）
- **技術棧**：Flask + Chart.js（純前後端，無框架）

---

## 架構概覽

```
GitHub Actions（每日定時）
  ├── yfinance 拉報價（在 GitHub VM 上跑，不被 Yahoo 擋）
  ├── Alpha Vantage 備援（美股）
  └── 推 cache_data.json → GitHub repo
                              │
Render（純唯讀快取伺服器） ◄───┘ 啟動時從 GitHub 拉快取
  └── 回傳報價/走勢給瀏覽器

Cloudflare → DNS 解析 stock.cyclinebot.uk → Render
```

---

## 檔案結構

| 檔案 | 用途 |
|------|------|
| `app.py` | Flask 後端（純讀快取，不打外部 API） |
| `index.html` | 前端儀表板（Chart.js 圖表） |
| `scripts/refresh_quotes.py` | 每日報價刷新腳本（GitHub Actions 執行） |
| `.github/workflows/daily-refresh.yml` | 定時排程設定 |
| `requirements.txt` | Python 依賴（flask, gunicorn, requests） |
| `Procfile` | Render 啟動指令 |
| `runtime.txt` | Python 3.11.11 |
| `cache_data.json` | 報價快取（存在 GitHub repo 裡） |

---

## API 端點

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/` | 回傳儀表板頁面 |
| POST | `/api/quote` | 讀取快取報價，未快取的自動加入追蹤 |
| POST | `/api/history` | 讀取快取走勢資料（1d/5d/1mo） |
| POST | `/api/reload-cache` | 從 GitHub 重新拉快取到記憶體 |
| GET | `/api/sources` | 快取狀態與設定 |
| GET | `/api/debug-github` | GitHub API 連線診斷 |

---

## 前端功能

- **自動市場判斷**：純數字（2330）→ 台股，英文（AAPL）→ 美股
- **摘要列**：美股 (USD) / 台股 (TWD) 分開統計（市值、損益、成本、漲跌）
- **圓餅圖**：持股配置、產業分布、損益分布（按市場分開）
- **投資組合走勢圖**：美股/台股各自獨立，支援 5D/1M/3M/6M/1Y/MAX 縮放
- **未實現損益柱狀圖**：每檔股票的損益（綠漲紅跌）
- **個股走勢小圖**：日/週/月 tab 切換，顯示區間漲跌幅
- **持倉明細表**：市場 badge、現價、持股、成本、損益、漲跌、行內編輯、移除
- **損益隱藏**：眼睛 icon 切換，預設模糊隱藏所有金額（市值、成本、損益）
- **Toast 通知**：報價狀態、錯誤提示、來源顯示
- **響應式設計**：桌面 6 欄 → 平板 3 欄 → 手機 2 欄
- **持股資料**：存在瀏覽器 localStorage（各裝置獨立）

---

## 報價更新機制

- **GitHub Actions 定時排程**：
  - UTC 06:00（週一至五）→ 台股收盤後
  - UTC 21:00（週一至五）→ 美股收盤後
  - 可手動從 GitHub Actions 頁面觸發
- **資料來源**：yfinance（主要）→ Alpha Vantage（美股備援）
- **快取內容**：報價 + 5 分鐘盤中走勢 + 5 日走勢 + 1 月走勢
- **快取 TTL**：24 小時
- **刷新後**：自動通知 Render reload 記憶體快取

---

## 環境變數

### Render

| 變數 | 說明 |
|------|------|
| `GITHUB_TOKEN` | GitHub API 存取 token |
| `GITHUB_REPO` | `ChungYuCheng/us_stock_dashboard` |
| `QUOTE_CACHE_TTL` | 報價快取 TTL（預設 86400 秒） |
| `HISTORY_CACHE_TTL` | 走勢快取 TTL（預設 86400 秒） |

### GitHub Secrets（Actions 用）

| Secret | 說明 |
|--------|------|
| `CACHE_TOKEN` | GitHub PAT（讀寫 cache_data.json） |
| `ALPHA_VANTAGE_API_KEY` | Alpha Vantage API key（備援報價） |

---

## 部署流程

### 1. Render 設定

1. 到 [render.com](https://render.com) 用 GitHub 帳號登入
2. New → Web Service → 連結 `us_stock_dashboard` repo
3. 設定：
   - Runtime: Python
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT`
   - Plan: Free
4. Environment 加入 `GITHUB_TOKEN` 和 `GITHUB_REPO`

### 2. Cloudflare DNS 設定

| 欄位 | 值 |
|------|-----|
| Type | `CNAME` |
| Name | `stock` |
| Target | `us-stock-dashboard.onrender.com` |
| Proxy status | DNS only（灰色雲朵） |

### 3. GitHub Secrets 設定

到 repo → Settings → Secrets and variables → Actions → 新增 `CACHE_TOKEN` 和 `ALPHA_VANTAGE_API_KEY`

### 4. 更新流程

```bash
# 切換帳號
gh auth switch --user ChungYuCheng

# 推送更新（Render 自動重新部署）
cd ~/stock-dashboard
git add -A && git commit -m "描述變更" && git push

# 切回工作帳號
gh auth switch --user cycheng_momo
```

### 5. 手動刷新報價

```bash
export GITHUB_TOKEN="your_token"
export GITHUB_REPO="ChungYuCheng/us_stock_dashboard"
python3 scripts/refresh_quotes.py
```

---

## 已知限制

1. **Render Free Plan**：閒置 15 分鐘休眠，首次訪問需 30-50 秒喚醒
2. **持股資料不跨裝置**：存在 localStorage，換裝置需重新輸入
3. **報價非即時**：每天更新兩次（台股/美股收盤後各一次）
4. **台股報價來源單一**：只有 yfinance，無備援
