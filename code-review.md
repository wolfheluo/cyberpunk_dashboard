# Code Review: Quant Fleet Dashboard (cyberpunk_dashboard)

> **審查日期**：2026-08-08  
> **範圍**：`quant_fleet_server.py`、`cyberpunk_dashboard.html`、`strategies/*.py`、`i18n/*.json`、`init_db.py`、`auth_new.py`  
> **排除**：資安面向（依指示不考量）

---

## 摘要

| 等級 | 數量 | 說明 |
|------|------|------|
| 🔴 Critical | 4 | API 路由錯誤（功能無法運作）、ZeroDivisionError、資料對齊錯誤 |
| 🟠 Major | 9 | 假 KPI 計算、死代碼、CSS 顏色錯誤、API 設計缺陷 |
| 🟡 Minor | 8 | 效能浪費、硬編碼、UI 不一致、缺失 i18n |
| 🔵 Info | 5 | 架構建議、可擴展性、命名建議 |

---

## 🔴 Critical

### 1. `do_GET()` 中的 POST/DELETE 路由永遠不會觸發（Watchlist 新增/刪除功能完全損壞）

**檔案**：`quant_fleet_server.py` — L436-453  
**問題**：`http.server.SimpleHTTPRequestHandler` 根據 HTTP method 分派：GET → `do_GET()`、POST → `do_POST()`。但 `/api/symbols/add`（POST）和 `/api/symbols/*`（DELETE）的路由寫在 `do_GET()` 內，且用 `self.command == "POST"` / `self.command == "DELETE"` 做判斷 — 在 `do_GET()` 中 `self.command` 永遠是 `"GET"`。

```python
# ❌ 在 do_GET() 內，self.command 永遠是 "GET" —— 永遠不會進入
elif self.path == "/api/symbols/add" and self.command == "POST":
    ...
elif self.path.startswith("/api/symbols/") and self.command == "DELETE":
    ...
```

**影響**：前端 `addSymbol()` 發 POST `/api/symbols/add` → 落入 `do_POST()` 的 `else: self.send_error(404)`。`removeSymbol()` 發 DELETE → 無 `do_DELETE()` handler → 405。**Watchlist 管理功能完全無法使用。**

**建議**：將 POST 路由移至 `do_POST()`，新增 `do_DELETE()` 方法處理刪除。

---

### 2. `vol_surge` 計算可能觸發 ZeroDivisionError

**檔案**：`quant_fleet_server.py` — L204  
**問題**：

```python
"vol_surge": len(closes_1h) >= 2 and volume > (sum(float(k[5]) for k in klines_1h[-10:]) / min(len(klines_1h[-10:]), 1)) * 1.5,
```

當 `klines_1h` 為空列表時，`klines_1h[-10:]` 是 `[]`，`len` 為 0，`min(0, 1)` = 0，`sum(...) / 0` → **ZeroDivisionError**。雖然前面有 `len(closes_1h) >= 2` 的短路保護，但 `closes_1h` 來自 klinds response，兩者數量可能不一致（API 回傳但解析失敗時）。

**建議**：改用 `max(len(klines_1h[-10:]), 1)`。

---

### 3. Backtest 回測 equity curve 與 dates 陣列長度不一致

**檔案**：`quant_fleet_server.py` — L400-403  
**問題**：

```python
step = max(1, len(equity_curve) // 200)
sampled_eq = equity_curve[::step]          # 從全部取樣（包含 warmup）
sampled_dates = dates[WARMUP_DAYS::step][:len(sampled_eq)]  # 從 warmup 後取樣
```

`equity_curve` 包含 WARMUP_DAYS（30）筆 warmup 記錄，但 `dates` 被 `[WARMUP_DAYS::step]` 切片後只剩 N 筆。`sampled_eq` 有 30+N//step 筆，`sampled_dates` 最多 N//step 筆 → **兩個陣列長度不一致**，前端繪圖時 X/Y 軸資料錯位。

**建議**：統一取樣來源 — `sampled_eq = equity_curve[WARMUP_DAYS::step]`。

---

### 4. `/api/symbols` GET 路由在 `do_GET()` 中有冗餘檢查但功能正常，對比之下 add/delete 卻完全損壞

**檔案**：`quant_fleet_server.py` — L436-453  
**影響**：`/api/symbols` GET 可正常運作，但 `/api/symbols/add` POST 和 `/api/symbols/{sym}` DELETE 完全失效。使用者看到的行為：點「+ ADD」沒反應、點「✕」刪除沒反應。

**建議**：重構為正確的 method dispatch。

---

## 🟠 Major

### 5. KPI 計算全部是假數據 — Sharpe、Win Rate、Max Drawdown

**檔案**：`quant_fleet_server.py` — L309-321  
**問題**：

```python
# L311: win_rate 未使用 closed trades 資料
win_rate = min(95, 50+pnl/100)  # ← 完全捏造，與實際交易勝率無關

# L316: sharpe 是線性變換 pnl/5000
"sharpe": round(1.5+(pnl/5000), 2)  # ← 非真實 Sharpe ratio（需 return/std）

# L319: max_drawdown 不是回撤，只是 pnl*0.6 百分比
"max_drawdown": round(max(0.5, abs(pnl)/INITIAL_CAPITAL*100*0.6), 1)
```

這些數字顯示在儀表板的 KPI 區塊（Sharpe、Win Rate、Max DD），但全部不是真實計算。使用者看到的數字沒有實際意義。

**建議**：
- Win Rate = `winning_trades / total_closed_trades * 100`
- Sharpe = 從 equity curve 計算 daily returns 的 mean/std
- Max Drawdown = 從 equity curve 追蹤 peak-to-trough

---

### 6. `do_GET()` 處理路徑過於混亂 — POST/PATCH/DELETE 路由混在 GET handler 中

**檔案**：`quant_fleet_server.py` — L416-487  
**問題**：`http.server` 的設計是 method dispatch（`do_GET`、`do_POST`），但本專案把所有路由都塞進 `do_GET`，再用 `self.command` 區分。這導致上述 Critical #1。更糟的是，`/api/trade/simulate`、`/api/reset`、`/api/strategy/*` 等 POST 路由寫在 `do_POST`，但 symbols 的 POST/DELETE 卻在 `do_GET`。路由邏輯不一致。

**建議**：統一規範 — 所有 GET 路由在 `do_GET()`，所有 POST/PUT/DELETE 在 `do_POST()` + 新增 `do_DELETE()`。

---

### 7. `auth_new.py` — 完全無關的殘留檔案

**檔案**：`/root/auth_new.py`（148 行）  
**問題**：這是 `project-manager` 的 Flask auth blueprint。匯入 `Flask`、`config.ADMIN_PASSWORD` 等不存在的依賴。與 Quant Fleet 專案完全無關。

**建議**：刪除。已被 git status 標為 untracked，移除即可。

---

### 8. `deleteStrategy()` 在前端定義但無 UI 觸發入口

**檔案**：`cyberpunk_dashboard.html` — L339-346  
**問題**：`deleteStrategy(fname)` 函數已正確實現，但 `loadStrategyList()`（L311-320）渲染策略卡片時沒有加入刪除按鈕。使用者無法從 UI 刪除策略。

**建議**：在策略卡片加入刪除按鈕，或在編輯器標題欄加入刪除按鈕（注意：需先切換 active strategy）。

---

### 9. `loadBacktest()` 函數定義但從未被呼叫

**檔案**：`cyberpunk_dashboard.html` — L362-364  
**問題**：
```javascript
function loadBacktest(){
  drawBTCanvas();
}
```
此函數只呼叫 `drawBTCanvas()`，但 `runBacktest()` 結尾也呼叫了 `drawBTCanvas()`。且 `loadBacktest` 本身從未被任何事件綁定或呼叫。

**建議**：刪除（殘留代碼）。

---

### 10. CSS `.glow-green` 和 `.dot-green` 使用青色而非綠色

**檔案**：`cyberpunk_dashboard.html` — L17-20  
**問題**：
```css
.glow-green{text-shadow:0 0 6px #00E5FF40,0 0 1px #00E5FF}  /* ← 是 cyan，不是 green */
.dot-green{display:inline-block;...background:#00E5FF;...}    /* ← 也是 cyan */
```

`.glow-green` 和 `.dot-green` 的顏色是 `#00E5FF`（cyan），而非 `#00FF66`（green）。名稱誤導。且在 `renderConnection()` 中使用 `dot-green`（青色），與 LIVE 狀態的青色 `dot-cyan` 視覺上完全相同，無法區分。

**建議**：統一為一套 dot 類別，或用正確顏色。

---

### 11. `execute_trade` 缺少交易失敗時的 rollback / 錯誤處理

**檔案**：`quant_fleet_server.py` — L111-157  
**問題**：`execute_trade` 使用多個 `db_conn.execute()` 呼叫（INSERT trade、UPDATE portfolio、UPDATE/DELETE positions）。這些呼叫之間沒有 transaction 包裝。如果中間某個 execute 拋出例外，前面的變更已自動提交（WAL 模式 auto-commit），導致資料不一致（例如：扣了現金但沒記錄 trade）。

**建議**：在 `with db_lock:` 內用 `db_conn.execute("BEGIN IMMEDIATE")` / `db_conn.commit()` 包裝整個交易流程。

---

### 12. 前端 `renderTickerRail()` 中 ticker 卡片不顯示策略名稱

**檔案**：`cyberpunk_dashboard.html` — L471-481  
**問題**：ticker 卡片顯示 symbol、name、價格、漲跌幅、volume 和 sparkline，但不顯示該幣種的當前 signal 來源策略。使用者無法從 ticker rail 知道哪個策略產生了 BUY/SELL 訊號。

**建議**：在卡片中加入 signal badge（例如：`BUY · Bollinger`）。

---

### 13. `/api/backtest/run` 使用 `db_conn.execute()` 而無 `db_lock`

**檔案**：`quant_fleet_server.py` — L567-588  
**問題**：backtest 端點在無 `db_lock` 保護下讀取 `historical_klines` 表。如果同時有 `fetch_all_data()` 在寫入（雖然實際上不會寫這張表），或未來有其他 writer，會導致資料競爭。當前影響較小但架構不嚴謹。

**建議**：讀取操作可用 shared lock 或確保無並發寫入。

---

## 🟡 Minor

### 14. `fetch_all_data()` 每個 tick 發送 1 + 2N 次 HTTP 請求

**檔案**：`quant_fleet_server.py` — L163-205  
**問題**：對 7 個 symbol，每次 refresh 發送 1（ticker/24hr）+ 14（每個 symbol 2 個 klines）= 15 次 HTTP 請求。每個請求有 ~200ms latency，總耗時 ~2-3 秒。Binance API 有 rate limit，高頻請求可能被限制。

**建議**：考慮 WebSocket 串流、使用 `ticker/price` 批量端點、或增加 refresh interval。

---

### 15. 策略矩陣（strategy_matrix）硬編碼只有第一個策略 active

**檔案**：`quant_fleet_server.py` — L287-293  
**問題**：
```python
for si,sn in enumerate(strategy_names):
    for ti,tf in enumerate(timeframes_list):
        active = (si == 0)  # 永遠只有第一個策略 active
        cells.append([si,ti,"active" if active else "idle"])
```

策略×時間框架矩陣只是視覺填充 — 永遠只有 index=0 的策略所有時間框架為 "active"，其他全為 "idle"。與實際策略執行無關。

**建議**：要麼連接真實的策略×時間框架評估結果，要麼簡化為純視覺裝飾並加註解。

---

### 16. `SYMBOL_NAMES` 型態不一致

**檔案**：`quant_fleet_server.py` — L20-25  
**問題**：
```python
SYMBOLS, SYMBOL_NAMES = [], []   # ← SYMBOL_NAMES 初始為 list
def reload_symbols():
    global SYMBOLS, SYMBOL_NAMES
    ...
    SYMBOL_NAMES = {r[0].replace("USDT",""): r[1] for r in rows}  # ← 變成 dict
```

初始宣告為 `[]`（list），`reload_symbols()` 重新賦值為 `{}`（dict）。Python 允許但型態不一致。

**建議**：初始值改為 `{}`。

---

### 17. `runBacktest()` 前端按鈕狀態未在完成後恢復

**檔案**：`cyberpunk_dashboard.html` — L365-408  
**問題**：L371 的 `btn.disabled=false` 引用了未定義的 `btn` 變數。`runBacktest` 函數內沒有獲取按鈕元素，導致錯誤分支中 `btn` 是 undefined → 在前端 console 拋出 ReferenceError（被 catch 捕捉到 st 顯示，但按鈕仍 disabled）。

**建議**：修正為 `document.getElementById('btnRunBacktest')` 或移除不存在元素的引用。

---

### 18. Dashboard 右側 `navTotal`（AUM）只在儀表板渲染時更新

**檔案**：`cyberpunk_dashboard.html` — L154  
**問題**：`navTotal` 顯示總資產，但只在 `renderKPIs()` → `R()` → `fetchData()` 鏈中更新（儀表板頁面）。切換到 Account 頁時 `loadAccount()` 重新 fetch 但不更新 `navTotal`。

**建議**：在 `loadAccount()` 中也更新 `navTotal`。

---

### 19. Sidebar `WATCHLIST` 導航項目缺少 `data-i18n` 屬性

**檔案**：`cyberpunk_dashboard.html` — L101  
**問題**：
```html
<div class="nav-item" data-page="watchlist" onclick="switchPage('watchlist')">
  <span class="icon">📋</span><span class="nav-label">WATCHLIST</span>
</div>
```
`WATCHLIST` 沒有 `data-i18n` 屬性 → 切換到中文時仍顯示英文。

**建議**：加入 `data-i18n="nav_watchlist"` 並在 i18n JSON 補上對應翻譯。

---

### 20. `quant_fleet.db-shm` 和 `quant_fleet.db-wal` 出現在 untracked files

**檔案**：git status  
**問題**：SQLite WAL 模式的暫存檔案（-shm、-wal）不應進入版本控制。`.gitignore` 已忽略 `quant_fleet.db` 但沒忽略這兩個。

**建議**：`.gitignore` 加入 `quant_fleet.db-shm` 和 `quant_fleet.db-wal`。

---

### 21. 策略檔案的 `composite` 分數未被前端使用

**檔案**：`strategies/*.py`（三個策略都回傳 `composite`）  
**問題**：每個策略的 `evaluate()` 都計算並回傳 `composite` 分數，但前端沒有使用這個值。雖然在訊號日誌中有 `factors` dict，但 `composite` 本身未顯示在任何地方。

**建議**：如果 composite 有意義，在 signal table 或 log 中顯示；否則移除計算。

---

## 🔵 Info

### 22. 架構建議：`http.server` → FastAPI

`http.server` 是單線程同步伺服器。如果一個 `/api/data` 請求耗時 5 秒（Binance API 延遲），其他請求會排隊等待。對於多用戶或多頁面同時操作的情境，建議遷移到 FastAPI + uvicorn（async 支援）。

---

### 23. `now_ts()` 函數設計問題

**檔案**：`quant_fleet_server.py` — L14-15  
**問題**：
```python
def now_ts(fmt="%H:%M:%S"):
    return datetime.now().strftime(fmt)
```
預設格式只有 `%H:%M:%S`（無日期）。在日誌中使用時（L217、L262-280），跨日的 log 無法區分日期。

**建議**：預設格式改為 `"%m-%d %H:%M:%S"`。

---

### 24. `_BASE` 目錄計算方式可簡化

**檔案**：`quant_fleet_server.py` — L27、`init_db.py` — L7  
**問題**：
```python
_BASE = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else os.getcwd()
```
`'__file__' in dir()` 檢查是為了處理 interactive shell 的情況。但 `dir()` 只返回 local scope，在 module-level `__file__` 應該永遠存在。`'__file__' in dir()` 在 module scope 實際上是檢查 `__file__` 是否在 local namespace（一般來說是）。

**建議**：直接使用 `os.path.dirname(os.path.abspath(__file__))`，並在最外層 catch `NameError`。

---

### 25. `init_db.py` `HIST_END` 硬編碼為 `(2026, 6)` 需手動更新

**檔案**：`init_db.py` — L15  
**問題**：歷史資料下載範圍寫死 `(2026, 6)`。每個月需要手動更新。

**建議**：改為 `datetime.now()` 推算上個月。

---

### 26. `bar_chart.html`、`bar_chart.png` 殘留測試檔案

**檔案**：`/root/bar_chart.html`、`/root/bar_chart.png`  
**問題**：與 Quant Fleet 專案無關的測試檔案，出現在 untracked files。

**建議**：刪除或加入 `.gitignore`。

---

## 檔案總覽

| 檔案 | 行數 | 狀態 | 摘要 |
|------|------|------|------|
| `quant_fleet_server.py` | 607 | 活躍 | 主要後端。含路由錯誤、假 KPI、交易原子性問題 |
| `cyberpunk_dashboard.html` | 692 | 活躍 | 前端儀表板。含死代碼、CSS 顏色錯誤、缺失 i18n |
| `strategies/bollinger_mean_reversion.py` | 38 | 活躍 | 策略 — 邏輯正常 |
| `strategies/ema_crossover_trend.py` | 36 | 活躍 | 策略 — 邏輯正常 |
| `strategies/momentum.py` | 31 | 活躍 | 策略 — 邏輯正常 |
| `init_db.py` | 152 | 活躍 | DB 初始化 + 歷史資料下載 |
| `i18n/en.json` | 92 | 活躍 | 英文翻譯 — 缺少 `nav_watchlist` |
| `i18n/zh.json` | 92 | 活躍 | 中文翻譯 — 缺少 `nav_watchlist` |
| `auth_new.py` | 148 | 🔴 死代碼 | 來自 project-manager，與本專案無關 |
| `bar_chart.html` / `.png` | - | 🔴 殘留 | 測試檔案 |

---

## 修復優先級建議

1. **🔴 立即修復**：#1（Watchlist API）、#2（ZeroDivisionError）、#3（Backtest 資料錯位）
2. **🟠 盡快修復**：#5（假 KPI）、#10（CSS 顏色）、#11（交易原子性）
3. **🟡 排程修復**：#15（策略矩陣）、#19（i18n 缺失）、#20（.gitignore）
4. **🔵 長期改善**：#22（FastAPI 遷移）、#14（WebSocket 串流）
