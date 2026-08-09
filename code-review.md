# Quant Fleet / Cyberpunk Dashboard — 完整 Code Review

> 審計日期：2026-08-09 | 審計範圍：全專案 13 個檔案（~3,600 行）
> 審計方式：fresh clone → 全檔閱讀 → 靜態追蹤 → 本機 runtime 重現驗證
> 版本：`4de8af7`（main）

---

## 概覽

| 嚴重度 | 數量 | 重點 |
|--------|------|------|
| 🔴 Critical | 2 | `/api/reset` 404、`/api/trade/simulate` 誤觸發帳戶清空 |
| 🟠 Major | 7 | 估值 key 錯配、vol_surge 恆真、價格延遲、回測 lookahead、XSS 邊緣、HEAD 目錄洩漏、POST 壞 JSON 500 |
| 🟡 Minor | 12 | 見下方清單 |
| 🔵 Info | 8 | 文件/架構/測試現況 |

**驗證環境**：本機 `QF_DB_PATH=/tmp/qf_test.db QF_PORT=8901` 啟動真實 server，以 curl + sqlite 讀回驗證；`py_compile`、`node --check` 全數通過；en/zh i18n 149 keys 完全對齊。

**核心發現**：commit `360ffe2`（batch 4a 修復）在重構 `/api/trade/simulate` 時，誤把 `elif self.path=="/api/reset":` 分支頭刪除，導致 reset 邏輯被併入 simulate 分支。一個錯誤同時弄壞兩個功能：RESET 按鈕 404、模擬交易執行後帳戶被瞬間清空。

---

## 🔴 Critical

### C-1. `/api/reset` endpoint 消失 — 前端 RESET 按鈕完全失效

**檔案**：`quant_fleet_server.py` L1162-1171（reset 邏輯被併入 simulate 分支）、`dashboard/js/app.js` L780（呼叫端）

**問題**：`do_POST` 中 `elif self.path=="/api/reset":` 分支頭在 commit `360ffe2` 被誤刪，reset 程式碼本體殘留在 `/api/trade/simulate` 分支內。現在 `POST /api/reset` 沒有任何 handler → 落到 `else: self.send_error(404)`。

**驗證（runtime）**：
```
POST /api/reset          → HTTP 404（前端 resetAccount() 拿 HTML error page → .json() 拋錯 → 靜默失敗）
```

**影響**：
- 前端 `resetAccount()`（app.js L778-784）呼叫 `/api/reset`，404 的 HTML body 讓 `r.json()` 直接 reject，無 `.catch` → unhandled rejection，帳戶頁 RESET 按鈕「按了沒反應」。
- README L123 仍記載 `POST /api/reset` — 文件與實作脫節。

**修復建議**：把 L1162-1171 的 reset 區塊移回獨立分支：
```python
elif self.path == "/api/reset":
    with db_lock:
        get_db().execute("DELETE FROM trades")
        get_db().execute("DELETE FROM positions")
        get_db().execute("DELETE FROM signals")
        get_db().execute("UPDATE portfolio SET cash=?, updated_at=datetime('now', '+8 hours') WHERE id=1", (INITIAL_CAPITAL,))
        get_db().commit()
    with log_lock: exec_log.clear()
    global active_strategy; active_strategy = ""
    self._json(200, {"status": "reset", "capital": INITIAL_CAPITAL, "active_strategy": ""})
```

---

### C-2. `/api/trade/simulate` 執行交易後立刻清空整個帳戶 + 單一請求送出兩次 HTTP response

**檔案**：`quant_fleet_server.py` L1161-1171

**問題**：C-1 的誤併入導致 simulate 分支變成「先執行模擬交易，接著無條件 DELETE 全部 trades/positions/signals、重置 cash、清 exec_log、清 active_strategy」。此外 L1162 `self._json(200, ...)` 送出第一次 response 後，L1171 又呼叫一次 `self._json(200, {"status":"reset"...})` — 同一請求寫入兩份 HTTP response（協議污染，client 可能收到 garbage 或連線異常）。

**驗證（runtime）**：
```
POST /api/trade/simulate {"symbol":"BTCUSDT","side":"BUY","price":100}
  → HTTP 200 {"status":"filled","trade_id":1,"quantity":5.0,"notional":500.0,...}
  → 但 DB 檢查：trades=0, positions=0, signals=0, cash=10000.0   ← 交易被瞬間抹除
```

**影響**：
- simulate 功能等於「交易 → 立刻被 reset」——完全無法使用。
- 雙重 `_json` 使 HTTP/1.0 連線（`SimpleHTTPRequestHandler` 預設）在正常 response 後又收到第二份 response bytes；部分 client（keep-alive、測試 harness）會解析失敗或連線錯亂。

**修復建議**：與 C-1 同源——把 reset 區塊移回 `/api/reset` 分支，simulate 分支只保留執行 + 單次 `_json` 回傳。

---

## 🟠 Major

### M-1. `_position_value_now()` 用錯誤的 price_map key — 帳戶估值永遠 fallback 到 entry price

**檔案**：`quant_fleet_server.py` L665-676

**問題**：`price_map` 的 key 是完整 symbol（`t["symbol"]` = `"BTCUSDT"`），但 `positions.symbol` 存的是去掉 USDT 的 `"BTC"`（`execute_trade` 收到的 `sym`）。`price_map.get(r[0])` 永遠 miss → 永遠用 `entry_price` 估值。對比 `fetch_all_data` L708 的 re-mark 正確使用 `r[0] + "USDT"`——此處漏了後綴。

**驗證（runtime）**：插入 `BTC` 2 units @ entry 100、mock ticker24 現價 120 → `_position_value_now()` 回傳 **200**（entry 估值）而非正確的 **240**。

**影響**：`/api/portfolio` 的 `position_value`/`total_equity` 使用開倉價而非市價——帳戶頁與 dashboard（`/api/data` 的 `pos_value` 正確）顯示不一致，違反 N-6「用 live prices」的修復意圖。

**修復建議**：`px = price_map.get(r[0] + "USDT")`，或直接改讀 `positions.current_price`（每 poll 已 re-mark）。

---

### M-2. `vol_surge` 單位錯配 — 幾乎永遠為 True，VOLUME factor 恆為 1.0

**檔案**：`quant_fleet_server.py` L764

**問題**：
```python
vol_surge = len(closes_1h) >= 2 and volume > (sum(float(k[5]) for k in (klines_1h or [])[-10:]) / max(len(klines_1h[-10:]), 1)) * 1.5
```
`volume` 是 **24 小時** quote volume（`pm["volume"]`，ticker24 欄位），而右側是最近 **10 根 1h klines** 的平均單小時量。24h 總量 ≈ 24 × 平均小時量 → 幾乎恆大於 1.5 倍 → `vol_surge` 幾乎恆 True → `/api/data` 的 VOLUME factor（L980）恆為 1.0，策略拿到的 `indicators.volSurge` 失去鑑別度。

**影響**：依賴 `volSurge` 的策略（如 OBI 型放量條件）在 live 端看到的是無意義的恆真旗標；`/api/params/ref` 對 volSurge 的描述（"Volume > 1.5x recent 1h average"）與實作不符。

**修復建議**：改為比較「最近一根 1h kline 的量」對「前 N 根平均」：
```python
k = klines_1h or []
if len(k) >= 3:
    last_v = float(k[-1][5]); prev_avg = sum(float(x[5]) for x in k[-11:-1]) / 10
    vol_surge = last_v > prev_avg * 1.5
```

---

### M-3. Server 端策略執行價格延遲最多 60 秒（與設計文件矛盾）

**檔案**：`quant_fleet_server.py` L683-691、L748

**問題**：`fetch_all_data` 的 `price_map` 完全來自 `fetch_ticker24_cached()`（TTL 60s）。`fetch_book_cached()`（TTL 3s）只被用來建 `book` 參數，**沒有**用來更新 `ticker.price`。因此策略評估與 `execute_trade` 執行的價格最多落後 60 秒，而前端 WS 顯示的是即時價。

**影響**：
- 文件（cyberpunk-dashboard skill）宣稱「ticker.price is live every poll」——實作並非如此。
- 對 0.1% 步距的網格策略是實質問題：BTC 60 秒內移動 0.1%+ 很常見，網格觸發/成交價可能整個 level 錯位，`grid.js` 的 `center/step` 邏輯會拿舊價做決策。
- 前端 WS 即時更新 `DATA.tickers[i].price` 只是顯示層，策略與成交仍是舊價 → UI 與實際執行明顯不一致。

**修復建議**：每 poll 用 `fetch_book_cached()` 的 mid price（`(best_bid+best_ask)/2`，3s TTL）覆寫 `price_map[sym].price`，或新增 `/api/v3/ticker/price`（weight 2）短 TTL cache。

---

### M-4. 回測 `atr14` lookahead bias — 使用整個資料集的「最後 14 根」而非「到目前為止」

**檔案**：`strategies/_run_backtest.js` L103（與 L66-75 `calcATR`）

**問題**：
```javascript
atr14: calcATR(klines, 14),
```
`calcATR` 內部 `trs.slice(-period)` 取的是**整個 klines 陣列**的最後 14 根 TR——在 bar `i` 時，這代表 bar `i` **之後**（資料集末端）的波動率。每一根 bar 拿到的 `atr14` 都是同一個「整段資料最後 14 根」的 ATR 值，等同偷看未來。

**影響**：任何使用 `atr14` 的策略（停損/波動率過濾）回測結果失真且無法復現——live 端 `calc_atr(klines_1h, 14)` 用的是「截至現在」的 klines，兩者語意完全不同。

**修復建議**：傳入截至目前的 slice：
```javascript
atr14: calcATR(klines.slice(0, i + 1), 14),
```

---

### M-5. 策略檔名未驗證即進入前端 inline onclick — 潛在 stored XSS（低利用性）

**檔案**：`quant_fleet_server.py` L98-114（`list_js_strategies`）、`dashboard/js/app.js` L76-78

**問題**：`list_js_strategies()` 只過濾 `.js` 後綴與 `_` 前綴，**不**套用 `_safe_strategy_name()` 白名單。若 `strategies/` 目錄被放入含引號的檔名（如 `a".js`、`x'.js`——本機檔案系統可存在），前端 `onclick="openStrategy('...filename...')"` 與 `deleteStrategy` 會直接內插未轉義字串 → HTML attribute breakout → XSS。寫入路由（create/save/activate）已驗證檔名，唯獨「列出」路徑沒驗證。

**影響**：需要本機檔案系統寫入權限才能投放惡意檔名，利用性低；但該 server 綁 0.0.0.0 且無 auth（M-3 已接受風險），屬於 defense-in-depth 缺口。前端 `esc(s.filename)` 可兜底。

**修復建議**：
- Server：`list_js_strategies` 用 `_safe_strategy_name` 過濾或 skip 不合規檔名；
- 前端：`esc(s.filename)` 包住所有內插點（onclick、option value）。

---

### M-6. `do_HEAD` 未覆寫 — 洩漏專案根目錄列表

**檔案**：`quant_fleet_server.py` L1017-1129（`Handler` 覆寫 do_GET/do_POST/do_DELETE，唯獨未覆寫 do_HEAD）

**問題**：`SimpleHTTPRequestHandler.do_HEAD` 走預設檔案伺服邏輯：`HEAD /` → 對專案根目錄做 directory listing（HTTP 200 + Content-Length 669 = listing HTML），`HEAD /dashboard/` 同樣回 listing。`GET /` 被自訂邏輯擋住（只給 dashboard HTML），但 HEAD 完全繞過。

**驗證（runtime）**：
```
HEAD /           → HTTP 200, Content-type: text/html, Content-Length 669（目錄列表）
HEAD /dashboard/ → HTTP 200, Content-Length 379（子目錄列表）
HEAD /api/data   → HTTP 404（走檔案系統 translate_path，與 GET 行為不一致）
```

**影響**：LAN 上任何人可列舉專案檔案樹（quant_fleet.db 是否存在、strategies/、backtest_data/ 等）——資訊洩漏，與 D1 的 static-path 邊界保護精神不符。

**修復建議**：覆寫 `do_HEAD` 使行為與 `do_GET` 一致（或直接 `def do_HEAD(self): self.do_GET()` 後端由框架剝離 body）。

---

### M-7. 所有 POST endpoint 對 malformed JSON 無防護 — 直接 500/連線錯誤

**檔案**：`quant_fleet_server.py` L1133、1148、1173、1209、1263（`json.loads(self.rfile.read(...))`）

**問題**：每個 POST handler 都直接 `json.loads(...)`，無 try/except。Content-Length 缺失或 body 非合法 JSON → `json.loads` 拋 `ValueError`/`json.JSONDecodeError` → handler 未捕獲 → 連線異常中斷（client 看到空回應/connection reset），server log 無記錄。

**影響**：robustness 缺口；測試 harness、curl 誤用、或掃描器發送 malformed body 時無法得到結構化錯誤回應。

**修復建議**：包一層 `_read_json()` helper：
```python
def _read_json(self):
    try:
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
    except Exception:
        return None
```
各 handler `if body is None: self._json(400, {"error": "Invalid JSON"}); return`。

---

## 🟡 Minor

| ID | 檔案/位置 | 問題 | 建議 |
|----|-----------|------|------|
| N-1 | `_run_backtest.js` L236 | 缺 `req.strategy` 檔名 regex 驗證（`_run_strategy.js` L18 有，此處漏）— N-22 defense-in-depth | 加上 `/^[A-Za-z0-9_-]+\.js$/` |
| N-2 | `_run_backtest.js` L137-199 | 回測缺 live 端 `MIN_CASH`(1000) gate — cash 低於 1000 時 live 拒絕開新倉、回測照開 | 對齊 live 語意 |
| N-3 | `_run_backtest.js` L142 | cover 時現金不足**靜默跳過**（live 端是 `rejected` event）— 回測/實盤事件語意分歧 | 回測標記 rejected 或至少註記 |
| N-4 | `quant_fleet_server.py` L120-125 vs L963-965 | `_log_warn` cap 300（刪前 100）與 `fetch_all_data` cap 200（pop 最舊）不一致 | 統一為單一常數 |
| N-5 | `quant_fleet_server.py` L968 | `strategy_matrix` 只取 `list_js_strategies()[:4]` — 超過 4 個策略時矩陣不完整 | 全量或加 pagination |
| N-6 | `quant_fleet_server.py` L1000 | `kpi.pnl_day` 實為總損益（`total_equity - INITIAL_CAPITAL`）非當日 — 命名誤導 | 改名 `pnl_total` 或實作日損益 |
| N-7 | `quant_fleet_server.py` L1052-1055 | static 路由 `.html`/`.json` 被當 `text/plain`（ct 對應只處理 css/js） | `.html` → `text/html`、`.json` → `application/json` |
| N-8 | `dashboard/js/app.js` L780-783 | `resetAccount()` 無 `.catch` — C-1 修復後仍建議加錯誤提示 | 加 `.catch` 顯示失敗 |
| N-9 | `dashboard/js/app.js` L574 | client hot-reload 傳 `volume_m`（百萬）而 server 傳原始 `volume` — 策略在 client/server 看到的 volume 單位不同 | 統一單位 |
| N-10 | `dashboard/js/app.js` L222-262 | `Math.min.apply(null, allEq)` 對極大陣列可能 RangeError（目前規模安全） | 迴圈比較 |
| N-11 | `dashboard/js/app.js` L477-478 | `renderExecLog` 每次 render 強制 scroll 到底 — 使用者無法往上捲讀舊 log | 僅在已貼底時才自動捲 |
| N-12 | `dashboard/cyberpunk_dashboard.html` L19 | 標題仍寫 "QUANT FLEET v2.5"（skill 記載已 v2.10）— 版本標籤漂移 | 更新或從 config 讀取 |
| N-13 | `quant_fleet_server.py` L937 | `price < 1 and 4 or 2` 三元短路寫法可讀性差（decimal 位數 hack） | 改 `4 if price < 1 else 2` |
| N-14 | `quant_fleet_server.py` L722 | `datetime.utcnow()` 在 Python 3.12+ 已 deprecated（僅 warning，功能正常） | 改 `datetime.now(timezone.utc)` |

---

## 🔵 Info

| ID | 位置 | 說明 |
|----|------|------|
| I-1 | `init_db.py` L96 | **Timestamp 除數矛盾（既有警示）**：程式註解稱 Vision zips 為 13 位 ms（`//1000`），但 cyberpunk-dashboard skill 記載舊經驗為 16 位 µs（`//1_000_000`）。本次無法下載真實 zip 驗證；若 `python init_db.py` 出現 "year out of range"，需還原為 `//1_000_000` |
| I-2 | repo root | `tests/` 套件已於 commit `4de8af7` 移除（spec "Further Notes" 生命週期決策）— 目前無自動測試 |
| I-3 | `quant_fleet_server.py` L1292 | `Access-Control-Allow-Origin: *` + 綁 0.0.0.0 + 無 auth — README 已記載為 accepted risk（D20 option d） |
| I-4 | `quant_fleet_server.py` L116-118 | `_strategy_state`/`_strategy_mtime` 為 in-memory dict、無 lock — 多 tab 併發 poll 有輕微 race（mtime 檢查 + pop 非原子）；實務影響低 |
| I-5 | `quant_fleet_server.py` L150 | 每次 `/api/data` poll spawn 一個 node subprocess — 1s poll 可接受；多 tab 會放大（skill 已註記） |
| I-6 | `quant_fleet_server.py` L717-722 | `prices` 表每 5 分鐘 ×7 symbol 寫入 → ~2,000 rows/day，無清理機制（長期運作會緩慢增長） |
| I-7 | `strategies/_run_backtest.js` L10-12 | `INITIAL_CAPITAL`/`TRADE_SIZE_PCT` 在 JS helper 重複定義，與 Python 端數值需人工同步 — 建議由 server 注入 |
| I-8 | `README.md` L116、L135-140 | README 結構：L133 後直接接 L135 的「Strategy Return Values」標題，`## Writing a Strategy` 段落被截斷（markdown 排版問題） |

---

## 已驗證為正確（避免誤報）

| 項目 | 驗證結果 |
|------|----------|
| Path traversal 防禦（`_safe_strategy_name` + `_safe_static_path`） | 通過 — 全部策略路由與 static 路由有 realpath 邊界檢查 |
| XSS 轉義 `esc()`（`& < > " '`） | 通過 — D18 修復完整，symbols/add 也有字元白名單 |
| `execute_trade` 四分支狀態機（開/加/平/補） | 通過 — 與 skill 記載語意一致，`size_pct` SELL 分支已傳（D7） |
| `portfolio_stats` per-symbol MTM（`last_price[sym]`） | 通過 — 無單一價格污染 |
| `rebuild_cycles` stale loop var | 通過 — `pos = {"symbol": sym,...}` 與 `cur_price.get(p["symbol"])` 均正確 |
| 訊號表 bloat 防護 | 通過 — 僅非 HOLD/WAIT 寫入 `signals` |
| `_fetching` reentrancy guard + `visibilitychange` | 通過 — 雙路徑釋放、前景立即 fetch（app.js L801） |
| WS 雙 stream 獨立 backoff（N-16） | 通過 — 各 stream 自帶 `wsReconnect[kind]` |
| en/zh i18n 對齊 | 通過 — 149 keys 完全一致（腳本驗證） |
| `py_compile` / `node --check` | 通過 — 全部檔案語法正確 |
| `close_pct`/`size_pct` clamp（live + backtest） | 通過 — 0.01-1.0 / 0.001-0.5 一致 |

---

## 總結

本次為 fresh audit（不參考先前 ledger）。最嚴重的是 **commit `360ffe2` 引入的回歸**：reset 分支頭被誤刪，一次弄壞 `/api/reset` 與 `/api/trade/simulate` 兩個功能，並在單一請求中寫入兩份 HTTP response。其餘 Major 集中在**價格資料流**（M-1 估值 key 錯配、M-2 vol_surge 單位、M-3 60s 價格延遲、M-4 回測 lookahead）——這些會讓「帳戶頁 vs dashboard」顯示不一致、策略指標失真、回測結果不可信，建議優先處理。

| 嚴重度 | 數量 |
|--------|------|
| 🔴 Critical | 2 |
| 🟠 Major | 7 |
| 🟡 Minor | 14 |
| 🔵 Info | 8 |

修復順序建議：C-1/C-2（同一 diff 可一次修復）→ M-1/M-4（正確性）→ M-2/M-3（指標與價格流）→ 其餘。

*本文件為審計 ledger：每項修復後更新狀態（✅/⏳）並保留至使用者確認移除。*
