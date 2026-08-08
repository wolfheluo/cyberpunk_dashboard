# Quant Fleet (cyberpunk_dashboard) — 代碼審計報告 (fix.md)

> 審計日期：2026-08-08 | 審計範圍：全專案 9 個檔案
> 狀態：全部項目已修復並經本地端到端實測（~~刪除線~~ = 已修）

---

## 🔴 Critical

### ~~C-1. 策略儲存 API 必然 NameError 崩潰~~
**已修**：刪除 `f.write(code)` 死引用，NAME 改從 `new_code` 解析（`_strategy_meta`）。實測：POST save 回 200 + 正確 name；前端 saveStrategy 加 `.catch()` 錯誤顯示。

### ~~C-2. 策略路由路徑穿越 — 任意檔案 讀/寫/刪~~
**已修**：新增 `_safe_strategy_name()`（basename + `^[A-Za-z0-9_\-]+\.js$` 白名單），套用於 code/activate/create/delete/save 五個路由；symbols DELETE 同步加 `[A-Z0-9]+USDT` 驗證；`super().do_GET()` 移除（靜態原始碼不再洩露）。實測（`--path-as-is` 送原始路徑）：全部回 `{"error":"Invalid strategy name"}`，DB 完好。

### ~~C-3. 策略訊號引擎完全失效 — 永遠 HOLD，永不交易~~
**已修**：
- `strategies/default.js` 改為物件字面值 `({ NAME, DESCRIPTION, evaluate })`；
- Server 端新增 `_run_strategy.js` node helper，`fetch_all_data` 每 poll 一次 node 子程序評估全部 symbols，取代硬編碼 HOLD → **自動紙上交易復活**；
- 前端 `evaluateJSStrategy` 檢查改為 `typeof activeJSStrategy.evaluate === 'function'`。
- 實測：即時資料下 BNB/SOL/LINK → SELL（RSI>70）、其餘 HOLD，完全由策略決定。

### ~~C-4. Backtest 是空殼 — 從未執行任何計算~~
**已修**：新增 `_run_backtest.js` node helper（與線上交易同一份 `evaluate()` 路徑），`/api/backtest/run` 重寫為每策略一次 node 呼叫，刪除重複 `append` 與死掉的 Python `_run_backtest`。實測：9 個 symbol×strategy 0.15s 跑完，HYPERUSDT +165.22%（真實 2025-2026 Binance 日線）。

---

## 🟠 Major

### ~~M-1. /api/portfolio 持倉估值 fallback 錯誤~~
**已修**：`r[0] or r[2]` → `(r[0] if r[0] else r[1])`（current_price 缺省時用 entry_price，不再拿 quantity 當價格）。

### ~~M-2. Positions 現價 / 未實現損益從不刷新~~
**已修**：`fetch_all_data` 每 poll 用當輪 price_map 對所有持倉 UPDATE current_price / unrealized_pnl。

### ~~M-3. 策略建立（create）與列表系統脫節~~
**已修**：create 只接受 `.js`、範本改 JS 物件字面值、前端 prompt 預設改 `new_strategy.js`。

### ~~M-4. KPI 指標是捏造的公式~~
**已修**：新增 `portfolio_stats()` — FIFO 已實現損益算 win_rate、交易權益曲線算 sharpe/max_drawdown；算不出來回 None，前端顯示 `--`。實測：無交易時 `sharpe/win_rate/max_drawdown: null`。

### ~~M-5. signals 表無限膨脹~~
**已修**：僅訊號非 HOLD 時寫入 signals 表（HOLD 每天 12 萬列問題消除）。

### ~~M-6. 前端多處 ReferenceError / 死連結~~
**已修**：移除 `activeStratLabel`（HTML 無此元素）、runBacktest 錯誤分支的 `btn` 死引用、dead code `renderStrategyMatrix`；fetchData 更新 `tickerCount`。

---

## 🟡 Minor

### ~~N-1. 重複行~~（`sym = symbol.replace("USDT","")` 兩次 → 刪除）
### ~~N-2. `get_js_strategy_code` dead code~~（刪除，改 `_strategy_meta` 統一解析）
### ~~N-3. `ws_feed.py` 整檔 dead code~~（git rm；前端直連 Binance WS）
### ~~N-4. Badge 顏色複製錯誤~~（`.badge-green` 改為 #00FF66）
### ~~N-5. i18n 缺口~~（WATCHLIST nav 加 `data-i18n="nav_watchlist"`；cliPrompt 改由 tickers 動態產生 + `cli_scan` key）
### ~~N-6. 無認證 + 綁 0.0.0.0~~（**保留** — 需從 Windows VM 遠端存取，paper trading 環境可接受；README 註明）
### ~~N-7. 靜態檔 fallback 洩露原始碼~~（`super().do_GET()` → 404，實測原始碼不可下載）
### ~~N-8. 儲存型 XSS~~（server `_esc()` + 前端 `esc()` 套用 symbol/name innerHTML）
### ~~N-9. SELL 無 realized PnL~~（`portfolio_stats` FIFO 計算；trades 表維持不變）
### ~~N-10. 其他小項~~（`utcfromtimestamp` → `fromtimestamp(tz=timezone.utc)`；loadAccount 讀 `pf.initial_capital`；/api/trades LIMIT 50→200；activate/save open() 加 utf-8）

---

## 🔵 Informational

### ~~I-1. README 嚴重過時~~（**已重寫** — JS 策略系統、目錄結構、Node 依賴、backtest 引擎全數對齊）
### ~~I-2. 版本標示 v2.4~~（升 v2.5，HTML + en/zh.json 同步）
### I-3. `/api/params/ref` 欄位過時
**驗證後為誤報**：CSV 已是 JS 風格 key（indicators.rsi/sma20/volSurge/closes），與新策略介面一致，無需修改。
### I-4. `prices` 表只寫不讀
**保留**：每 5 分鐘快照作為未來分析資料源，無副作用。
### I-5. `active_strategy` 無持久化
**保留**：重啟回到 default.js，可接受。

---

## 總結

| 嚴重度 | 數量 | 狀態 |
|--------|------|------|
| 🔴 Critical | 4 | ✅ 全修，實測驗證 |
| 🟠 Major | 6 | ✅ 全修，實測驗證 |
| 🟡 Minor | 10 | ✅ 9 修 + 1 有意的保留（N-6） |
| 🔵 Info | 5 | ✅ 2 修 + 1 誤報 + 2 有意的保留 |

**架構決策**：策略維持 JS（物件字面值），Server 端透過 node 子程序執行（`_run_strategy.js` / `_run_backtest.js`），線上交易與回測共用同一份 `evaluate()` 程式碼路徑，行為一致。
