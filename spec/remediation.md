# Remediation Spec — cyberpunk_dashboard 審計修補

> Spec 狀態：ready-for-agent | 來源：code-review.md（2026-08-09 完整審計）+ 使用者決策會談
> 發布方式：本 repo 文件追蹤（GitHub issue 需 token，環境不可用；與 code-review.md 同一流程）

---

## Problem Statement

2026-08-09 完整 code review 發現 5 Critical / 10 Major / 27 Minor / 13 Info。其中：

- **路徑穿越**：`/dashboard/` 與 `/i18n/` 兩條靜態路由可下載完整交易 DB（950KB）與主機任意檔案（root 身分）— 實測成功
- **資料正確性**：init_db.py 毫秒時間戳除錯（日期全落 1970）；backtest 未平倉空頭清算符號錯誤（final_equity 高估 2×notional）
- **功能缺損**：SELL 開/加空漏 size_pct（空頭網格金字塔失效）；prices 表 5 分鐘節流因時區混用失效；backtest 引擎不支援 add/close_pct/size_pct（grid 回測與 live 脫節）
- **可靠度**：HTTPServer 單執行緒（backtest 期間整站凍結）；node 策略失敗靜默吞掉；/save 無語法檢查；active_strategy 預設指向已刪除的 default.js（平台開機靜默停擺）
- **前端**：儲存型 XSS 家族（watchlist/策略名/回測名未逸出、esc() 不轉引號）；I18n.init 無 catch（i18n 載入失敗整機死機）

## Solution

依使用者決策逐項修補（決策會談結論）：

1. **C-3（策略檔刪除）為設計** — 不預設套用策略，使用者自行新增。不恢復檔案；改為 server 啟動預設 `active_strategy=""`、README 移除「內建 default.js」宣稱
2. **Threading 採方案 C** — ThreadingHTTPServer + `threading.local` 每執行緒獨立 SQLite 連線（SQLite 內建跨連線鎖，WAL 安全，不需補齊 db_lock）
3. 其餘 Critical/Major/Minor 依報告修補（見 Implementation Decisions）

## User Stories

1. As 平台使用者，I want 靜態路由無法讀取資料庫與主機檔案，so that 交易資料與伺服器不被未認證下載
2. As 平台使用者，I want 全新安裝執行 init_db.py 後回測日期正確，so that backtest 範圍與曲線不失真
3. As 平台使用者，I want 回測結束仍持空倉時 final_equity 正確，so that 回測排名不誤導
4. As 平台使用者，I want 空頭網格加倉遵循 size_pct 遞增（2%→6%），so that 多空部位規模對稱
5. As 平台使用者，I want prices 表按 5 分鐘節流記錄，so that DB 不無限膨脹
6. As 平台使用者，I want backtest 期間 dashboard 仍可回應，so that 頁面不凍結
7. As 平台使用者，I want grid.js 回測反映 add/close_pct/size_pct 行為，so that 回測與 live 一致
8. As 平台使用者，I want server 重啟/存檔後網格倉位不被整筆全平，so that 持倉狀態穩定
9. As 平台使用者，I want 儲存有語法錯誤的策略時收到錯誤回饋，so that 平台不靜默停擺
10. As 平台使用者，I want 啟動時無策略檔時平台明確顯示「無策略」，so that 不靜默全 HOLD
11. As 平台使用者，I want watchlist/策略名稱/回測結果中的惡意 HTML 不執行，so that 頁面安全
12. As 平台使用者，I want i18n 載入失敗時仍能以英文啟動，so that 頁面不整機死機
13. As 平台使用者，I want 空頭回補現金不足時收到 rejected，so that 網格狀態不漂移
14. As 平台使用者，I want Binance 短暫抖動時回退過期快取，so that 頁面不整頁 502
15. As 平台使用者，I want bookTicker 有短 TTL 快取，so that 多分頁不觸發 Binance 限流
16. As 平台使用者，I want signals 表不無限增長，so that DB 不膨脹
17. As 平台使用者，I want 重新 activate 策略時狀態乾淨，so that 舊語義不污染新邏輯
18. As 平台使用者，I want 帳戶頁與儀表板權益數字一致，so that 不困惑
19. As 平台使用者，I want 交易量大時 poll 不線性變慢，so that 掃描節奏穩定
20. As 平台使用者，I want 寫入型端點（reset/simulate/save）不可被同網段任意操控，so that 帳戶不被竄改
21. As 平台使用者，I want 前端所有渲染插值逸出，so that 無注入面
22. As 平台使用者，I want 時間戳全系統一致，so that 日誌與成交比對正確
23. As 平台使用者，I want README 與實作一致（無 default.js、有 grid.js、size_pct 文件化），so that 文件不誤導

## Implementation Decisions

### D1. 路徑穿越修補（C-1/C-2）
- 兩條靜態路由（`/dashboard/`、`/i18n/`）與第三條 `/dashboard/i18n/` 一併處理
- `os.path.realpath` 解析後檢查是否位於 `_BASE` 之下（prefix 比對）+ 副檔名白名單（.html/.js/.css/.json）
- 驗證：`curl --path-as-is` 送 6+ 組 traversal payload，全部預期 404/403，且 DB 不可下載

### D2. Threading（方案 C — 已決策）
- `HTTPServer` → `ThreadingHTTPServer`
- `threading.local` 每執行緒獨立 SQLite 連線（`get_db()` helper），**不**改 `check_same_thread`、不補 db_lock（SQLite 內建跨連線鎖 + WAL 保護）
- `db_conn` 全局引用遷移至 `get_db()`（約 20+ 處，機械作業）
- 既有 db_lock 保留（execute_trade/fetch_all_data 寫入序列化）
- 驗證：同時打 /api/data + /api/portfolio + /api/trades + /api/backtest/run 100 次壓力測試，無 sqlite 異常

### D3. C-3 為設計（已決策）
- 不恢復 default.js/OBI.js/ppmb.js（使用者刪除，策略由使用者自建）
- server 啟動預設 `active_strategy=""`（改自 "default.js"），啟動時印警告
- README 移除「內建 default.js」；Built-in Strategy 表改為「無內建策略，使用者自建」並補 grid.js 文件

### D4. init_db.py 時間戳（C-4）
- `int(row[0]) // 1_000_000` → `int(row[0]) // 1000`
- 完整性判斷 `existing > 300` 改為「最大日期 ≥ 預期範圍」（N-26 一併）
- 驗證：SELECT 抽查既有 DB 日期正確；全新 DB 模擬寫入日期落在 2025-2026

### D5. backtest 空頭清算（C-5）
- `_run_backtest.js` 結尾清算依 side 分向：SELL 時 `cash -= qty*close`、BUY 時 `cash += qty*close`
- 驗證：合成空頭持倉跑完回測，final_equity 正確（node 單測）

### D6. backtest 支援 add/close_pct/size_pct（M-8）
- `_run_backtest.js` 的 backtestOne 與 execute_trade 語義對齊：同向加倉（平均成本）、部分平倉（close_pct）、部位規模（size_pct）
- 驗證：grid.js 回測出現層級/遞增倉位（node 單測 + 回測數字與 live 對照）

### D7. SELL 開/加空補 size_pct（M-1）
- `fetch_all_data` SELL 開/加空分支加 `size_pct=sig.get("size_pct")`
- 驗證：空頭網格 dump 策略確認 lotSize 遞增（2%→6%）

### D8. prices 時區與節流（M-2 + I-13 最小範圍）
- 只修 prices 節流：`recorded_at` 與比對基準統一（Python 端 `datetime.now(timezone.utc)` 寫入或 SQLite `datetime('now')`）
- 全系統時間統一（I-13）列 Out of Scope（影響前端顯示時區，需另行決策）
- 驗證：1s poll × 10 次 → prices 表僅 1-2 列

### D9. cover short 現金不足（N-1）
- `qty = min(pos[1]*close_pct, cash/price)` 改為現金不足時回 `{"status":"rejected","reason":"insufficient_funds"}` + notional 下限檢查
- 驗證：cash 設低 → BUY cover → rejected 事件（curl）

### D10. 快取回退 stale（N-2）
- `fetch_ticker24_cached` / `fetch_klines_cached` 請求失敗回傳上次成功快取（記錄 stale 標記），完全無快取才回 None
- 驗證：斷網模擬（阻斷 Binance）→ /api/data 仍 200（stale 資料）

### D11. bookTicker 快取（N-3）
- 2-5s TTL 快取
- 驗證：連續 poll 觀察 Binance 請求數（weight 估算）

### D12. signals 表增長（N-4）
- WAIT 訊號不寫入（或合併），加容量上限/定期清理
- 驗證：1s poll × 60 次 → signals 表增量 < 閾值

### D13. _strategy_state activate 清除（N-5）
- activate 時一併 `_strategy_state.pop(fname, None)`（與 save 一致）
- 驗證：activate → 檢查 state 空

### D14. 權益計算一致（N-6）
- `/api/portfolio` 與 `/api/data` 統一用即時 price_map 計算（或共用函式）；修正 `r[0] if r[0] else r[1]` 的 current_price=0 退化
- 驗證：兩端點同時打，total_equity 一致

### D15. 效能（N-7）
- portfolio_stats/rebuild_cycles/equity_curve 增量維護或緩存（trades 變更才重算）；rebuild_cycles 單次遍歷
- 驗證：10k 筆 trades 下 poll 延遲 < 500ms

### D16. 死碼清理（N-8/I-12/I-4）
- 刪除 `_esc`、`add_log`、`global _trading_paused_until` 殘留、saveStrategy/loadActiveJSStrategy 的 no-op `replace(/\n/g,'\n')`

### D17. 錯誤可見性（N-9/M-10）
- `fetch_json` 失敗、node stderr、策略 ENOENT 寫入 exec_log 警告（不再全靜默）
- `/api/strategy/save` 前跑 `node --check`，失敗回傳錯誤
- 驗證：壞策略 save → 收到錯誤；刪除 active 策略 → UI 顯示「無策略」

### D18. XSS（M-6 + N-15）
- `esc()` 補轉 `"` 與 `'`
- 所有內插值過 esc()：loadStrategyList、renderBacktestResults、loadSymbols（含 data-sym 屬性）、renderRecentTrades、renderPositionsBar、renderSignalTable confidence、logLine 參數、帳戶表格
- server 端 watchlist add 的 name/symbol charset 白名單
- pipeline 節點半徑改用 index（0/7）比對（zh 失效問題一併修）
- 驗證：惡意 symbol/name POST → 頁面顯示逸出字元非執行

### D19. I18n.init 容錯（M-7）
- 加 .catch：失敗以英文啟動 + 排程重試
- 驗證：阻斷 i18n 路由 → 頁面仍啟動（英文）

### D20. M-3 無認證（決策：接受風險 — 2026-08-09 使用者拍板）
- 使用者決策：**d) 接受風險** — 不做認證/CORS 變更
- 風險接受理由：單人使用的本地紙交易儀表板；交易為模擬（無真實資金）；網路暴露面已由 D1（路徑穿越）、D18（XSS）、M-5（simulate 驗證）大幅收窄
- 殘留風險（已文件化於 README「Security Notes」）：同網段可讀取/寫入 API、無認證、CORS `*`
- 若日後部署至公網或多人使用，需重新評估（a/b/c）

### D21. Minor 批次
- N-3~N-27 逐項（見 code-review.md）：空倉曝險限制（N-10 文件標註）、positions round 一致（N-11）、_sma4h 標籤（N-12）、strategy_matrix active（N-13）、NAME regex 單引號（N-14）、WS 獨立重連+退避（N-16）、熱評估標示（N-17）、updateRailPrices data-price（N-18）、loadAccount catch（N-19）、硬編碼英文 i18n（N-20）、OBI 牆鐘 cooldown（N-21）、_run_strategy.js 檔名驗證（N-22）、state mtime 版本（N-23）、grid 死狀態 g.last（N-24）、backtest ticker 語義差異文件化（N-25）、README 更新（N-27）

## Testing Decisions

- **最高接縫 = HTTP API 層（curl）**：所有 server 修補以端點行為驗證（`curl --path-as-is` 測 traversal、並發壓力測 Threading、事件語義測 round-trip）
- **node 層單測**：策略與 backtest helper（_run_strategy.js/_run_backtest.js）直接 node 執行 — C-5/D6/D7 的核心驗證
- **靜態檢查**：`python3 -m py_compile` + `node --check` 每批修補後執行
- **既有先例**：always-BUY/always-SELL round-trip（開倉→平倉→KPI）、dump 策略驗證參數、`curl --path-as-is` 路徑穿越 — 皆為本專案已使用模式
- 修補分批進行，每批：修 → 驗證 → 更新 code-review.md（strikethrough）→ commit

## Out of Scope

- **C-3 策略檔恢復**（設計決定：不預設策略）
- **M-3 認證**（D20：**接受風險** — 使用者拍板選項 d，2026-08-09；風險已文件化）
- **全系統時間戳統一**（I-13 完整版：影響前端顯示時區，另案決策）
- 大架構改動：WebSocket 驅動 server、真實保證金/槓桿、歷史 tick 資料、策略 sandbox（vm 模組）
- 前端重構（框架化、CSP、Tailwind 本地打包）

## Further Notes

- code-review.md 為討論文件；修補完成且使用者確認後移除（與 fix.md 生命週期一致）
- 每個 Critical 修補需附實測證據（curl 輸出/壓力測試結果）於 commit message
- Threading 方案 C 的 `get_db()` 遷移為機械作業，建議以 sed/腳本批次處理後逐段驗證
