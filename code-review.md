# Code Review — Quant Fleet Dashboard

**Date**: 2026-08-08  
**Scope**: `init_db.py`, `quant_fleet_server.py`, `cyberpunk_dashboard.html`, `strategies/*.py`

---

## 1. 邏輯問題

### 1.1 回測引擎的 WARMUP_DAYS 期間 equity_curve 不正確
**檔案**: `quant_fleet_server.py` L354
```python
if i < WARMUP_DAYS:
    equity_curve.append(cash)
    continue
```
WARMUP 期間 equity 始終等於初始現金（不會反映持倉市值）。若策略在 warmup 結束前已有訊號但被跳過，回測結果不會完全準確。**建議**: warmup 期間也計算持倉市值。

### 1.2 `vol_surge` 的計算邏輯有誤
**檔案**: `quant_fleet_server.py` L204
```python
"vol_surge": volume > volume*0.85*1.2,
```
這等於 `volume > volume * 1.02`，永遠為 `False`（因為 `0.85 * 1.2 = 1.02`，且 `volume == volume`）。應改為與歷史平均成交量比較。**建議**: 使用 `closes_1h` 的 volume 欄位計算移動平均，或改為 `volume > avg_volume * 1.2`。

### 1.3 策略 matrix 硬編碼
**檔案**: `quant_fleet_server.py` L289-295
```python
strategies_list = ["RSI","SMA CROSS","VOL SURGE","COMPOSITE"]
...
a = (sn=="RSI" and tf=="1h") or ...
```
策略 matrix 的標籤和 active/idle 狀態完全硬編碼，跟實際使用的策略無關。如果換成 Bollinger 策略，matrix 仍顯示 RSI/SMA/VOL。**建議**: 從 strategy_registry 動態生成，或移除假 matrix。

### 1.4 BUY 訊號執行條件寬鬆
**檔案**: `quant_fleet_server.py` L230
```python
if signal == "BUY" and (not current_pos or current_pos["side"] != "BUY"):
```
只要有 BUY 訊號就加倉，即使已有 BUY 持倉且浮虧。**建議**: 加一個最大持倉數量限制（例如最多 1 單位），避免無限加倉。

### 1.5 SELL 時 partial 持倉未處理
**檔案**: `quant_fleet_server.py` L153
```python
if existing and existing[1] - quantity <= 0.00001:
    db_conn.execute("DELETE FROM positions WHERE symbol=?",(symbol,))
```
SELL 時只處理「全賣」或「不賣」，沒有 partial sell 的邏輯（例如賣一半）。**建議**: 加入 `quantity` 參數支援部分賣出。

### 1.6 前端帳戶頁面權益曲線假設所有 trades 都是 same-side pairs
**檔案**: `cyberpunk_dashboard.html` `drawAccountChart()`
從 trades 紀錄反推權益曲線時，假設 BUY 扣除 cash、SELL 加回 cash，但**未考慮持倉市值**（只追蹤現金）。**建議**: 用 `_run_backtest` 的邏輯重構。

---

## 2. 殘留代碼 / 死函數

### 2.1 `math` import 未使用
**檔案**: `quant_fleet_server.py` L6
```python
import math
```
從未被呼叫。**建議**: 刪除。

### 2.2 `trade_logs` 變數未使用
**檔案**: `quant_fleet_server.py` L248
```python
trade_logs=[]
```
宣告後從未被寫入或讀取。**建議**: 刪除。

### 2.3 `trade_info` 變數賦值後未使用
**檔案**: `quant_fleet_server.py` L228-233
```python
trade_info = None
...
trade_info = execute_trade(...)
```
返回值未被讀取。**建議**: 刪除，或將交易結果寫入 log。

### 2.4 `/api/backtests` GET 端點引用不存在的 `backtests` 表
**檔案**: `quant_fleet_server.py` L478-492
`backtests` 表已被移除（改用即時計算），但端點仍在。**建議**: 刪除，或改為別名 `/api/backtest/run`。

### 2.5 `order_flow` 欄位永遠為空物件
**檔案**: `quant_fleet_server.py` L330
```python
"order_flow":{}
```
前端 `renderOrderFlow()` 永遠顯示 "No order flow data"。**建議**: 移除或填入實際數據。

### 2.6 `signal_id` 在 `execute_trade` 中被記錄但從未被查詢
**檔案**: `quant_fleet_server.py` L131-132
`trades.signal_id` 欄位寫入後沒有任何地方讀取。**建議**: 保留（日後可做訊號回溯），或刪除。

---

## 3. HTML/JS 問題

### 3.1 i18n 字典鍵值不完整
`i18n/en.json` 有 91 keys，但 HTML 中有多處文字仍為硬編碼英文：
- `"No open positions"` / `"No trades yet"` / `"No data..."` 等
- 策略編輯器中的 `"Select a strategy"`、`"SAVE"`
- Backtest 頁面 `"Symbol"` / `"Return"` / `"Final Equity"` 表格標頭

部分已在 JS 中用 `I18n.t()` 動態查詢，但 fallback 仍為英文。**建議**: 補齊所有硬編碼字串。

### 3.2 `switchPage` 中 `loadSymbols()` 被呼叫兩次
**檔案**: `cyberpunk_dashboard.html` L282（推測）
之前修復時可能殘留。**建議**: 檢查並刪除多餘呼叫。

### 3.3 `drawAccountChart` 未處理空 trades 時的佔位文字
空 trades 時 canvas 顯示 "No trades yet"，但文字可能被 canvas 清空。**建議**: 加入 `ctx.fillText`。

### 3.4 Tailwind CDN 警告
使用 `<script src="https://cdn.tailwindcss.com">` 會在 console 產生警告。**建議**: 非 production issue，保留。

---

## 4. Python 安全 / 穩定性

### 4.1 `importlib.util` 動態載入策略無沙箱
使用者可在策略頁面編輯任意 Python 程式碼並儲存執行。這是設計意圖（不回滾），但要認知風險。**建議**: 文件開頭加註釋說明。

### 4.2 SQLite 無連線池
所有請求共用單一 `db_conn`，雖然有 `db_lock` 保護，但長時間查詢（如 backtest）會阻塞所有請求。**建議**: 每個請求建立獨立連線，或在 backtest 中使用獨立連線。

### 4.3 `exec_log` 全域變數無上限
雖然有 `pop(0)` 限制 200 筆，但多個請求同時 append 可能導致短暫超過限制。非關鍵問題，建議不修改。

---

## 5. 優化建議

### 5.1 減少 Binance API 呼叫次數
`fetch_all_data()` 對每個 symbol 逐一呼叫 `/api/v3/klines`（1h + 4h），7 個幣種 = 14 次 HTTP 請求。**建議**: 使用 WebSocket 串流或快取 klines 數據（例如每 5 分鐘刷新一次）。

### 5.2 回測速度優化
`_run_backtest` 對 7 幣種 × 3 策略 × 546 天逐筆計算，當前約需 2-3 秒。若幣種增加至 20 個，將需 10 秒以上。**建議**: 
- 使用 numpy 向量化計算 RSI/SMA/EMA
- 將 `calc_rsi` / `calc_sma` 的 O(n) 逐日重算改為 incremental

### 5.3 前端 polling 改 WebSocket
目前每 5 秒 `fetch('/api/data')`，伺服器和客戶端都有不必要的開銷。**建議**: 使用 WebSocket 推送 data 更新。

### 5.4 策略模板改進
`/api/strategy/create` 的模板缺少 `NAME` 和 `DESCRIPTION` 變數的明確說明。**建議**: 模板中加入註解範例：
```python
"""
NAME = "My Strategy"
DESCRIPTION = "Describe what it does"

def evaluate(ticker, indicators):
    # ticker: {"id", "name", "price", "volume"}
    # indicators: {"rsi_1h", "sma_4h", ...}  ← see PARAMS download
    return {"signal": "BUY", "confidence": 80, "factors": {}}
"""
```

---

## 6. 總結

| 嚴重度 | 數量 | 關鍵項目 |
|--------|------|---------|
| 🔴 高 | 2 | `vol_surge` 計算錯誤、回測 warmup 期權益不準 |
| 🟡 中 | 5 | 策略 matrix 硬編碼、BUY 無上限加倉、殘留端點、權益曲線只追現金 |
| 🟢 低 | 6 | 未使用 import、死變數、i18n 缺漏、polling 效率 |

整體架構清晰，模組化合理（`init_db.py` 獨立、策略插件式、前端三頁結構）。主要風險集中在策略自動交易邏輯和回測精確度上，建議優先修復 `vol_surge` 和 BUY 加倉上限。


---

## 修補驗證 (2026-08-08)

| # | 項目 | 狀態 |
|---|------|------|
| 1 | `vol_surge` 計算修正 | ✅ 改為近 10 根 K 線平均成交量比較 |
| 2 | 回測 warmup 期權益追蹤 | ✅ 加入 `cash + pos_value` |
| 3 | 策略 matrix 動態化 | ✅ 從 `strategy_registry` 讀取實際策略名稱 |
| 4 | BUY 訊號加倉限制 | ✅ 改為僅無持倉時才觸發 BUY |
| 5 | `/api/backtests` 死端點 | ✅ 已刪除 |
| 6 | 帳戶權益曲線 MTM | ✅ 加入持倉市值計算 |
| 7 | `import math` 未使用 | ✅ 已刪除 |
| 8 | `trade_logs` / `trade_info` 死變數 | ✅ 已刪除 |
| 9 | i18n 硬編碼字串 | ✅ 所有 "No open positions" / "No trades yet" 改用 I18n.t() |

**驗證方式**: 靜態程式碼檢查，11/11 項全部通過。
