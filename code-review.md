# Quant Fleet — 代碼審計報告 (code-review.md)

> 審計日期：2026-08-09 | 審計範圍：全專案 11 個檔案（quant_fleet_server.py 1168 行、app.js 786 行、HTML/CSS/i18n、strategies/、init_db.py、README）
> 審計方式：3 子代理平行深讀 + 關鍵發現實測驗證（路徑穿越/時區/清算符號）

---

## 🔴 Critical（安全漏洞 / 資料損壞 / 平台故障）

### C-1. 路徑穿越 — `/dashboard/` 靜態路由任意檔案讀取
**檔案**：`quant_fleet_server.py` 第 941 行
**問題**：`fpath = os.path.join(_BASE, self.path.lstrip('/'))` 無正規化/邊界檢查
**實測**：`curl --path-as-is '/dashboard/../quant_fleet.db'` → **200 + 完整 SQLite DB（950KB，含全部交易/持倉/資金）**；`'/dashboard/../../../etc/passwd'` → 200 讀取成功
**影響**：未認證遠端可下載整個交易資料庫與伺服器（root）可讀的任意檔案 — 嚴重資料外洩
**修復**：realpath 後檢查必須位於 `_BASE` 之下（prefix 比對）

### C-2. 路徑穿越 — `/i18n/` 路由任意檔案讀取
**檔案**：`quant_fleet_server.py` 第 935 行
**問題**：`fname` 無驗證直接 `os.path.join(_BASE, 'dashboard', 'i18n', fname)`
**實測**：`curl --path-as-is '/i18n/../../quant_fleet.db'` → 200 + 完整 DB（且標記為 JSON）
**影響**：同 C-1
**修復**：fname 白名單（僅 en.json/zh.json）或 realpath 邊界檢查

### C-3. 三支策略檔被誤刪（default.js / OBI.js / ppmb.js）
**檔案**：`strategies/`（commit `82ef484` diff 意外刪除）
**問題**：commit 82ef484（僅權益曲線樣式）的 `git add -A` 把三支策略檔的刪除一起納入 — 可能為 UI 測試期間誤刪。HEAD 只剩 grid.js
**影響**：server 啟動預設 `active_strategy="default.js"` 指向不存在的檔案 → **平台開機即靜默全 HOLD、自動交易停擺**；README 仍宣稱 default.js 為內建策略
**修復**（最優先）：`git checkout 82ef484^ -- strategies/default.js strategies/OBI.js strategies/ppmb.js` 恢復

### C-4. init_db.py 時間戳除錯 → 歷史資料全部落在 1970 年
**檔案**：`init_db.py` 第 122 行
**問題**：`ts = int(row[0]) // 1_000_000` — Binance openTime 為毫秒（13 位數），應 `// 1000`；現行使 1735689600000 → 1735689 秒 = 1970-01-21
**影響**：全新 clone 執行 `python init_db.py` 寫入整批 1970 日期 → backtest 範圍/曲線全毀（本機 DB 正確是因舊版資料 + `existing>300` 跳過重抓，bug 被遮蔽）
**修復**：`int(row[0]) // 1000`

### C-5. backtest 未平倉空頭清算符號錯誤
**檔案**：`strategies/_run_backtest.js` 第 167 行
**問題**：`if (position && klines.length) cash += position.qty * close` — 空頭開倉時 cash 已 `+notional`，平倉應 `cash -= qty*close`；且與 line 164 的 MTM（SELL 負號）不一致
**影響**：回測結束仍持空倉時 final_equity / total_return_pct 被**高估 2×notional**，回測排名失真
**修復**：依 side 分向 — SELL 時 `cash -= qty*close`、BUY 時 `cash += qty*close`

---

## 🟠 Major（功能缺損 / 安全弱點 / 一致性）

### M-1. SELL 開/加空路徑遺漏 size_pct
**檔案**：`quant_fleet_server.py` 第 778 行
**問題**：BUY 開/加多與 cover short 都有傳 `size_pct`，唯獨 SELL 開/加空沒傳 → 空頭永遠用固定 5%
**影響**：grid.js 空頭金字塔（2%×(1+0.25×(d-1)) 遞增）完全失效，多空部位規模不對稱
**修復**：`execute_trade(sym, "SELL", price, strategy_name, signal_id, size_pct=sig.get("size_pct"))`

### M-2. prices 表 5 分鐘節流因時區混用失效
**檔案**：`quant_fleet_server.py` 第 615 行
**問題**：`recorded_at` 用 SQLite `datetime('now','+8 hours')`（UTC+8），比對卻用 `datetime.now()`（UTC 主機）→ 恆差 8 小時 → 節流永不觸發
**影響**：1s poll 下 prices 表每秒插一列，DB 無限膨脹
**修復**：統一 UTC 基準

### M-3. 無認證 + 0.0.0.0 + CORS * + 可寫策略檔 → LAN 遠端程式碼執行
**檔案**：`quant_fleet_server.py` 第 1164 行
**問題**：所有端點無認證、綁 0.0.0.0、`Access-Control-Allow-Origin: *`；`/api/strategy/save` 可寫任意 .js（每 poll 被 node eval 執行）；`/api/reset` 可清空帳戶、`/api/trade/simulate` 可偽造交易（子代理實測 TESTFAKE 開倉成功）
**影響**：同網段任何機器/瀏覽器可操控帳戶、偽造交易、**寫入 JS 以伺服器身分執行（RCE）**
**修復**：至少限制寫入型端點僅 localhost / 加 token 認證 / 移除 CORS *

### M-4. HTTPServer 單執行緒 — 慢請求阻塞全站
**檔案**：`quant_fleet_server.py` 第 1164 行
**問題**：非 ThreadingHTTPServer；fetch_all_data 含同步網路呼叫（timeout 10s×3）+ node 子程序（15s）+ backtest 循序（120s×N）
**影響**：任一慢請求卡住 → /api/data、帳戶頁、靜態檔全部凍結
**修復**：改 ThreadingHTTPServer（db_lock/log_lock 已就緒）

### M-5. /api/trade/simulate 無輸入驗證
**檔案**：`quant_fleet_server.py` 第 1038 行
**問題**：symbol 不檢查 watchlist（TESTFAKE 可開倉）、price 任意、side 任意值落入 else 開空
**影響**：任何人可注入假交易污染帳戶/統計/權益曲線
**修復**：驗證 symbol ∈ watchlist、side ∈ {BUY,SELL}、price>0；或移除端點

### M-6. 儲存型 XSS 家族（前端）
**檔案**：`dashboard/js/app.js` 第 66/169/751 行
**問題**：策略 NAME/DESCRIPTION（loadStrategyList）、回測策略名（renderBacktestResults）、watchlist symbol/name（loadSymbols，`data-sym` 屬性可跳出）直接拼 innerHTML；`esc()` 只轉 `& < >` 不轉引號；server 端 watchlist name 完全無驗證
**影響**：watchlist/策略頁開啟即執行攻擊者腳本（LAN 內可達）
**修復**：esc() 補轉 `" '`；所有內插值過 esc()；server 端 name 白名單

### M-7. I18n.init() 無 .catch — i18n 載入失敗整機死機
**檔案**：`dashboard/js/app.js` 第 783 行
**問題**：init().then 無 catch；任一語言檔 fetch 失敗 → fetchData 永不啟動、永久 Initializing
**影響**：儀表板無恢復路徑
**修復**：加 .catch 以英文啟動 + 重試

### M-8. backtest 引擎不支援 add / close_pct / size_pct
**檔案**：`strategies/_run_backtest.js` 第 128 行
**問題**：add 無效（持倉時 BUY no-op）、close_pct 無效（一律全平）、size_pct 無效（固定 5%）
**影響**：grid.js 回測退化成「跌 0.1% 開倉、回中心全平」的 churn 機器，**回測與 live 嚴重脫節**（違背 README「same evaluate() path」宣稱）
**修復**：backtestOne 實作 add/close_pct/size_pct 與 execute_trade 對齊

### M-9. grid 狀態遺失後 recover 整倉全平
**檔案**：`strategies/grid.js` 第 41 行
**問題**：server 重啟/存檔清 _strategy_state → recover 分支設 levels=1 → 價格一回 center 即 close_pct=1/1 把累積多層倉位一次倒光
**影響**：任何重啟/編輯策略後已持網格倉位被整筆出場
**修復**：recover 依 position.quantity 反推 levels；至少避免 close_pct=1.0 全平路徑

### M-10. active_strategy 指向已刪檔案 + node 失敗靜默 + save 無語法檢查
**檔案**：`quant_fleet_server.py` 第 48/113/1081 行
**問題**：啟動預設 default.js 無存在性檢查（ENOENT 靜默回 {}）；run_js_strategy 失敗全靜默；/save 不跑 node --check
**影響**：策略壞掉時平台靜默停擺，無任何錯誤回饋
**修復**：啟動檢查檔案存在；save 前 node --check；失敗寫 exec_log 警告

---

## 🟡 Minor（程式碼品質 / 邊界 / 效能）

### N-1. cover short 現金不足時靜默部分回補
`quant_fleet_server.py` 第 280 行 — `qty = min(pos[1]*close_pct, cash/price)` 現金不足靜默部分成交且回 filled，無 notional 下限檢查 → grid 狀態與實際倉位漂移。應回 rejected 或明確回報成交 qty。

### N-2. Binance fetch 快取失敗不回退 stale 資料
`quant_fleet_server.py` 第 233 行 — 請求失敗回 None → 整頁 502 或 klines=None 使指標退化（sma=0/macd=0 → 策略誤判）。應回傳過期快取。

### N-3. bookTicker 每 poll 抓取無快取
`quant_fleet_server.py` 第 620 行 — weight≈4/call × 1s poll ≈ 240/min；多 tab 逼近 1200 上限。加 2-5s TTL。

### N-4. signals 表唯寫且無限增長
`quant_fleet_server.py` 第 726 行 — 1s poll × 7 symbols ≈ 60 萬列/日，全檔只有 INSERT 無 SELECT。WAIT 不寫或定期清理。

### N-5. _strategy_state 未在 activate 時清除
`quant_fleet_server.py` 第 1030 行 — re-activate 帶回舊 state（grid 舊 idx 語意污染新邏輯）。activate 一併 pop。

### N-6. /api/portfolio 與 /api/data 權益計算不一致
`quant_fleet_server.py` 第 925 行 — portfolio 用 re-mark 價、data 用即時價；current_price=0 時退化 entry。統一函式。

### N-7. 每秒全表 replay（效能）
`quant_fleet_server.py` 第 883 行 — portfolio_stats/equity_curve/rebuild_cycles 每次 poll 全表重播，O(n)/O(n²)；grid 高頻交易下 trades 快速累積。增量維護或緩存。

### N-8. 死碼：_esc / add_log / global pause 殘留
`quant_fleet_server.py` 第 17/52/1043 行 — `_esc` 無呼叫點、`add_log` 無呼叫者、`global _trading_paused_until` 指向未定義名字。刪除。

### N-9. fetch_json 裸 except 吞錯 + log_message 靜默
`quant_fleet_server.py` 第 216 行 — Binance 封鎖/node 錯誤/DB 異常全無跡可尋。失敗寫 stderr/exec_log。

### N-10. 做空無曝險限制
`quant_fleet_server.py` 第 323 行 — cash 隨做空增長可無限疊加空倉。限制總曝險或文件標註。

### N-11. positions 儲存未 round（與 trades 漂移）
`quant_fleet_server.py` 第 308 行 — trades round(qty,8) 但 positions 原始浮點，加倉/部分平倉後微漂移。

### N-12. `_sma4h` 欄位標籤誤導
`quant_fleet_server.py` 第 829 行 — 實際為 1h SMA20 卻命名 sma4h。改傳 indicators['sma_4h'] 或改名。

### N-13. strategy_matrix 硬編碼第一個策略為 active
`quant_fleet_server.py` 第 863 行 — `si==0` 與實際 active_strategy 無關，展示造假。

### N-14. 策略 NAME/DESCRIPTION regex 僅支援雙引號
`quant_fleet_server.py` 第 71 行 — 單引號字串的策略 meta 提取失敗。regex 改 `['"]`。

### N-15. 前端多處內插值未逸出（渲染層）
`dashboard/js/app.js` 第 286/400/419/432/445 行 — renderRecentTrades 的 symbol/side、renderPositionsBar、renderSignalTable confidence、logLine 參數未過 esc()；pipeline 節點以 label 比對 SIGNAL/DONE 切 zh 後失效（改用 index）。

### N-16. WS 雙 stream 重連互相拖累、固定 5s 無退避
`dashboard/js/app.js` 第 476 行 — 任一 stream 斷線關閉另一條健康連線。各自獨立重連 + 指數退避。

### N-17. 客戶端熱評估訊號與 server 執行訊號不一致
`dashboard/js/app.js` 第 518 行 — 前端用 WS 即時價 + buffer 重算 signal 覆寫 UI，server 用 klines 下單 → 顯示與成交可能矛盾。以 server 為準或標示預覽。

### N-18. updateRailPrices 依賴 span 順序猜價格格
`dashboard/js/app.js` 第 514 行 — `spans[spans.length-2]` 脆弱。加 data-price 屬性。

### N-19. loadAccount 無錯誤處理 → 白屏
`dashboard/js/app.js` 第 622 行 — 非 200 回應即 TypeError。加 .catch + 欄位防呆。

### N-20. 多處硬編碼英文未走 i18n
`dashboard/js/app.js` 第 765 行 + HTML — resetAccount confirm、statusBar Initializing、textarea/input placeholders、頂欄 // WATCHLIST（key 存在未引用）。

### N-21. OBI 加倉 cooldown 依賴牆鐘 Date.now()
`strategies/OBI.js`（82ef484^ 版本）第 61 行 — backtest 中永不過期、server 重啟後遺失。以 bar 序號取代。

### N-22. _run_strategy.js 未驗證策略檔名（縱深防禦缺失）
`strategies/_run_strategy.js` 第 18 行 — helper 應自行檢查 `^[A-Za-z0-9_-]+\.js$`。

### N-23. 策略 state 版本相容（改檔不清 state）
`strategies/_run_strategy.js` 第 22 行 — git pull/手動編輯改檔不清 state → 舊語義污染新 code。以 mtime/hash 判斷。

### N-24. grid.js 死狀態 g.last（只寫不讀）
`strategies/grid.js` 第 23 行 — 移除或實作原意。

### N-25. backtest ticker 與 live 語義差異
`strategies/_run_backtest.js` 第 111 行 — change_pct 恆 0、portfolio.total_equity 範圍不同、volSurge 閾值不同。README 註明差異清單。

### N-26. init_db.py existing>300 跳過下載，缺檔不補
`init_db.py` 第 92 行 — 新上架幣種/失敗月份永不補抓。以最大日期判斷完整性。

### N-27. README 過時
`README.md` 第 60/135/186 行 — 仍列 default.js 為內建（已刪）、未列 grid.js、漏 size_pct 文件。

---

## 🔵 Informational（文件 / 架構 / 建議）

- **I-1** 無 CSP + Tailwind CDN — 加 CSP；tailwind 本機打包
- **I-2** 25 個死 i18n key（en/zh 對齊但未引用）— 刪除或接回硬編碼處
- **I-3** `<html lang="en">` 固定、title 未地化 — setLang 同步
- **I-4** saveStrategy 的 `replace(/\n/g,'\n')` no-op 殘留碼 — 移除
- **I-5** hasOwnProperty 合併靜默丟 server 新欄位 — 明確欄位映射
- **I-6** loadActiveJSStrategy 開機重複 fetch（loadStrategies 內已呼叫）— 移除其一
- **I-7** radar 動畫在非 dashboard 頁背景持續跑 — 比照 pipeline 加 currentPage 檢查
- **I-8** I18n.t 參數 `${cap}` 約定脆弱 — 統一 `{cap}` + 參數 esc()
- **I-9** RSI 邊界 flip-flop（default.js）— 平多後 RSI>70 立即開空，無 cooldown
- **I-10** ppmb 純動能高 churn（DESCRIPTION 未註明）— 加 position 檢查或明示
- **I-11** 做空無保證金/強平（紙交易簡化）— 文件標註為設計
- **I-12** `_esc` 保留但無用 — 與 N-8 同
- **I-13** exec_log/DB/kline 三處時間基準混用（UTC / UTC+8 / UTC 日期）— 統一 UTC

---

## 總結

| 嚴重度 | 數量 | 關鍵項目 |
|--------|------|----------|
| 🔴 Critical | 5 | 路徑穿越×2（DB 洩漏）、策略檔誤刪、1970 日期、空頭清算符號 |
| 🟠 Major | 10 | size_pct 遺漏、時區節流失效、無認證 RCE、單執行緒、simulate、XSS、i18n 死機、backtest 語義、grid 全平、靜默故障 |
| 🟡 Minor | 27 | 上述 N-1 ~ N-27 |
| 🔵 Info | 13 | 上述 I-1 ~ I-13 |

**建議修復順序**：
1. **立即**：C-3 恢復策略檔（`git checkout 82ef484^ -- strategies/`）+ C-1/C-2 路徑穿越（realpath 檢查）
2. **高優先**：C-4、C-5、M-1、M-6（XSS）、M-10（靜默故障）
3. **中優先**：M-2 ~ M-5、M-7 ~ M-9
4. **低優先**：Minor 批次（死碼、i18n、逸出補齊、效能）

> 本報告為討論文件 — 修復需逐項與使用者確認後執行。
