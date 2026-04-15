# US Stock Dashboard — 部署流程與架構文件

## 架構總覽

```
┌──────────────┐     HTTPS      ┌────────────┐     CNAME      ┌──────────────┐
│  手機/電腦    │ ──────────────▶ │ Cloudflare │ ─────────────▶ │   Render     │
│  Chrome 瀏覽器│                 │   DNS      │                │  Web Service │
└──────────────┘                 └────────────┘                └──────┬───────┘
                                                                      │
                                                               ┌──────▼───────┐
                                                               │  Flask App   │
                                                               │  (gunicorn)  │
                                                               └──────┬───────┘
                                                                      │
                                                               ┌──────▼───────┐
                                                               │   yfinance   │
                                                               │  (Yahoo API) │
                                                               └──────────────┘

使用者持股資料：存在瀏覽器 localStorage（不經過伺服器）
```

## 技術架構

| 層級 | 技術 | 說明 |
|------|------|------|
| **前端** | HTML + Chart.js | 單頁儀表板，圖表渲染 |
| **後端** | Flask + gunicorn | 提供 API 與靜態頁面 |
| **資料來源** | yfinance (Yahoo Finance) | 即時報價 + 歷史股價 |
| **部署平台** | Render (Free Plan) | 自動從 GitHub 部署 |
| **DNS / SSL** | Cloudflare | 網域解析 + HTTPS 憑證 |
| **資料儲存** | 瀏覽器 localStorage | 持股資料存在使用者端 |

## 專案檔案結構

```
stock-dashboard/
├── app.py              # Flask 後端（API 路由）
├── index.html          # 前端儀表板頁面
├── requirements.txt    # Python 依賴套件
├── Procfile            # Render 啟動指令
├── runtime.txt         # Python 版本指定
├── .gitignore          # Git 忽略規則
└── DEPLOYMENT.md       # 本文件
```

## API 端點

| 方法 | 路徑 | 說明 | 參數 |
|------|------|------|------|
| GET | `/` | 回傳儀表板頁面 | - |
| POST | `/api/quote` | 取得即時報價 | `{ symbols: ["AAPL", "TSLA"] }` |
| POST | `/api/history` | 取得歷史股價 | `{ symbols: ["AAPL"], period: "5d" }` |

**period 可用值**：`1d`、`5d`、`1mo`、`3mo`、`6mo`、`1y`、`max`

---

## 部署流程

### 一、GitHub Repository

- Repo：`git@github.com:ChungYuCheng/us_stock_dashboard.git`（Private）
- 分支：`main`

### 二、Render 設定

1. 到 [render.com](https://render.com) 用 GitHub 帳號 (ChungYuCheng) 登入
2. **New → Web Service** → 連結 `us_stock_dashboard` repo
3. 填入以下設定：

| 欄位 | 值 |
|------|-----|
| **Name** | `us-stock-dashboard` |
| **Runtime** | Python |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `gunicorn app:app --bind 0.0.0.0:$PORT` |
| **Plan** | Free |

4. 點 **Deploy**，等待建置完成
5. 部署成功後會取得網址，格式如：`us-stock-dashboard-xxxx.onrender.com`

### 三、Render 綁定自訂網域

1. 進入 Render Service → **Settings → Custom Domains**
2. 點 **Add Custom Domain**
3. 輸入 `stock.cyclinebot.uk`
4. Render 會顯示需要設定的 DNS 記錄

### 四、Cloudflare DNS 設定

1. 登入 [dash.cloudflare.com](https://dash.cloudflare.com)
2. 選擇網域 **cyclinebot.uk**
3. 左側 **DNS → Records → Add Record**

| 欄位 | 值 |
|------|-----|
| **Type** | `CNAME` |
| **Name** | `stock` |
| **Target** | `us-stock-dashboard-xxxx.onrender.com`（Render 給的網址） |
| **Proxy status** | **DNS only**（灰色雲朵） |
| **TTL** | Auto |

> ⚠️ **Proxy status 必須設為灰色雲朵（DNS only）**，Render 需要自己處理 SSL 憑證，若開啟 Cloudflare Proxy（橘色雲朵）會導致 HTTPS 衝突。

4. 儲存後回到 Render 點 **Retry Verification**
5. 驗證通過後 Render 會自動配發免費 SSL 憑證

### 五、驗證部署

打開瀏覽器訪問：
```
https://stock.cyclinebot.uk
```

---

## 更新流程

程式碼修改後，推送到 GitHub 即自動重新部署：

```bash
# 切換 GitHub 帳號
gh auth switch --user ChungYuCheng

# 推送更新
cd ~/stock-dashboard
git add -A
git commit -m "描述你的變更"
git push

# 切回工作帳號
gh auth switch --user cycheng_momo
```

Render 偵測到 `main` 分支有新 commit 後會自動建置部署。

---

## 注意事項

1. **Render Free Plan 限制**：閒置 15 分鐘後服務會休眠，首次訪問需要約 30 秒冷啟動
2. **持股資料隱私**：資料存在瀏覽器 localStorage，不會上傳到伺服器，換裝置需重新輸入
3. **yfinance 限制**：Yahoo Finance 非官方 API，偶爾可能有速率限制或資料延遲
4. **GitHub Repo 為 Private**：程式碼不會公開，但即使公開也不含任何個人持股資料
