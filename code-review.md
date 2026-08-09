# Quant Fleet — 代碼審計報告 (code-review.md)

> 審計日期：2026-08-09 | 審計範圍：全專案 11 個檔案（quant_fleet_server.py 1168 行、app.js 786 行、HTML/CSS/i18n、strategies/、init_db.py、README）
> 審計方式：3 子代理平行深讀 + 關鍵發現實測驗證（路徑穿越/時區/清算符號）
>
---

## 🔎 修補狀態總覽（2026-08-09 二次驗證）

**驗證方式**：git 歷史比對（最新 code commit `4ec858a` 早於審計；working tree 乾淨，無未提交修補）+ 源碼逐項核對 + 實測（本專案 server 運行於 `0.0.0.0:8899`；8080 上的 React app 為另一專案，非本次目標）。

**結論**：**55 項中原 0 項已修補 → 修補進行中**（截至 2026-08-09：✅ 2 項完成，53 項待修）。修補藍圖 `spec/remediation.md`（commit `dd26637`，ready-for-agent）為執行依據；修補以 TDD 進行（tests/ 目錄，HTTP/node seam），每批完成後在本文件標記 ✅ 並 commit。

**實測證據（2026-08-09）**：
- C-1/C-2 路徑穿越 **仍可下載完整 950,272B 交易 DB**（`--path-as-is /dashboard/../quant_fleet.db`、`/i18n/../../quant_fleet.db` → 200，SQLite magic bytes 確認）
- M-3 `Access-Control-Allow-Origin: *` 仍在；監聽 `0.0.0.0:8899` 無認證
- M-5 POST /api/trade/simulate（TESTXUSDT, BUY, $1.0）→ `{"status":"filled","trade_id":998}` 無驗證開倉成功（測試資料已清理）

| 狀態 | 數量 | 項目 |
|------|------|------|
| ✅ 已修補（含測試證據） | 6 | C-1, C-2, M-5, M-6, M-7, N-15 |
| ⏳ 未修補（spec 已規劃未執行） | 43 | C-4, C-5, M-1, M-2, M-4, M-8 ~ M-10, N-1 ~ N-14, N-16 ~ N-20, N-22 ~ N-27, I-1 ~ I-8, I-12, I-13 |
| 🚫 設計決策：不恢復（配套待辦未完成） | 1 | C-3（active_strategy 仍 "default.js"、README 未更新） |
| ⏸ Decision Pending | 1 | M-3（spec D20，待使用者拍板） |
| ➖ 不適用（檔案已刪除） | 3 | N-21, I-9, I-10 |
| ✅ 設計維持 | 1 | I-11 |

**下一步**：依 spec/remediation.md D1 → D21 分批執行（每批：修 → 驗證 → 本文件標記 ✅ → commit），M-3 待使用者決策後補入。

---

## 🔴 Critical（安全漏洞 / 資料損壞 / 平台故障）

### C-1. 路徑穿越 — `/dashboard/` 靜態路由任意檔案讀取
**修補狀態**：✅ **已修補**（2026-08-09，spec D1）— tests/test_http.py TraversalTests 6/6 綠：4 組 traversal payload（`/dashboard/../quant_fleet.db`、`/dashboard/../../../etc/passwd`、`/i18n/../../quant_fleet.db`、`/dashboard/i18n/../../../../etc/passwd`）全數拒絕且無 SQLite/root 內容；合法靜態檔（app.js/style.css/en.json/zh.json）正常 200；實作 `_safe_static_path`（realpath 邊界檢查）+ `/dashboard/` 副檔名白名單（.html/.js/.css/.json）

**檔案**：`quant_fleet_server.py` 第 941 行
**問題**：`fpath = os.path.join(_BASE, self.path.lstrip('/'))` 無正規化/邊界檢查
**實測**：`curl --path-as-is '/dashboard/../quant_fleet.db'` → **200 + 完整 SQLite DB（950KB，含全部交易/持倉/資金）**；`'/dashboard/../../../etc/passwd'` → 200 讀取成功
**影響**：未認證遠端可下載整個交易資料庫與伺服器（root）可讀的任意檔案 — 嚴重資料外洩
**修復**：realpath 後檢查必須位於 `_BASE` 之下（prefix 比對）

### C-2. 路徑穿越 — `/i18n/` 路由任意檔案讀取
**修補狀態**：✅ **已修補**（2026-08-09，spec D1）— 同上測試：`/i18n/../../quant_fleet.db` 與 `/dashboard/i18n/../../../../etc/passwd` 全數拒絕；`/i18n/` 路由同樣經 `_safe_static_path` 限制於 i18n 目錄內

**檔案**：`quant_fleet_server.py` 第 935 行
**問題**：`fname` 無驗證直接 `os.path.join(_BASE, 'dashboard', 'i18n', fname)`
**實測**：`curl --path-as-is '/i18n/../../quant_fleet.db'` → 200 + 完整 DB（且標記為 JSON）
**影響**：同 C-1
**修復**：fname 白名單（僅 en.json/zh.json）或 realpath 邊界檢查

### C-3. 三支策略檔被誤刪（default.js / OBI.js / ppmb.js）
**修補狀態**：🚫 **決策：不恢復**（spec D3，使用者決策：無內建策略）— 但配套待辦未完成：server line 48 `active_strategy` 仍 `"default.js"`（應改 `""`），README line 60/188 仍宣稱 default.js 為內建策略；strategies/ 僅剩 grid.js 屬預期

**檔案**：`strategies/`（commit `82ef484` diff 意外刪除）
**問題**：commit 82ef484（僅權益曲線樣式）的 `git add -A` 把三支策略檔的刪除一起納入 — 可能為 UI 測試期間誤刪。HEAD 只剩 grid.js
**影響**：server 啟動預設 `active_strategy="default.js"` 指向不存在的檔案 → **平台開機即靜默全 HOLD、自動交易停擺**；README 仍宣稱 default.js 為內建策略
**修復**（最優先）：`git checkout 82ef484^ -- strategies/default.js strategies/OBI.js strategies/ppmb.js` 恢復

### C-4. init_db.py 時間戳除錯 → 歷史資料全部落在 1970 年
**修補狀態**：⏳ **未修補** — init_db.py line 122 仍 `int(row[0]) // 1_000_000`；spec D4 已規劃 `// 1000` + N-26 完整性判斷一併，未執行

**檔案**：`init_db.py` 第 122 行
**問題**：`ts = int(row[0]) // 1_000_000` — Binance openTime 為毫秒（13 位數），應 `// 1000`；現行使 1735689600000 → 1735689 秒 = 1970-01-21
**影響**：全新 clone 執行 `python init_db.py` 寫入整批 1970 日期 → backtest 範圍/曲線全毀（本機 DB 正確是因舊版資料 + `existing>300` 跳過重抓，bug 被遮蔽）
**修復**：`int(row[0]) // 1000`

### C-5. backtest 未平倉空頭清算符號錯誤
**修補狀態**：⏳ **未修補** — strategies/_run_backtest.js line 167 仍 `cash += position.qty * close`（無 side 分向）；spec D5 已規劃，未執行

**檔案**：`strategies/_run_backtest.js` 第 167 行
**問題**：`if (position && klines.length) cash += position.qty * close` — 空頭開倉時 cash 已 `+notional`，平倉應 `cash -= qty*close`；且與 line 164 的 MTM（SELL 負號）不一致
**影響**：回測結束仍持空倉時 final_equity / total_return_pct 被**高估 2×notional**，回測排名失真
**修復**：依 side 分向 — SELL 時 `cash -= qty*close`、BUY 時 `cash += qty*close`

---

## 🟠 Major（功能缺損 / 安全弱點 / 一致性）

### M-1. SELL 開/加空路徑遺漏 size_pct
**修補狀態**：⏳ **未修補** — quant_fleet_server.py line 778 SELL 開/加空仍無 `size_pct=` 參數；spec D7 已規劃，未執行

**檔案**：`quant_fleet_server.py` 第 778 行
**問題**：BUY 開/加多與 cover short 都有傳 `size_pct`，唯獨 SELL 開/加空沒傳 → 空頭永遠用固定 5%
**影響**：grid.js 空頭金字塔（2%×(1+0.25×(d-1)) 遞增）完全失效，多空部位規模不對稱
**修復**：`execute_trade(sym, "SELL", price, strategy_name, signal_id, size_pct=sig.get("size_pct"))`

### M-2. prices 表 5 分鐘節流因時區混用失效
**修補狀態**：⏳ **未修補** — line 615 仍 `datetime.now()`（UTC）vs `datetime('now','+8 hours')` 混用，prices 節流仍失效；spec D8 已規劃（最小範圍），未執行

**檔案**：`quant_fleet_server.py` 第 615 行
**問題**：`recorded_at` 用 SQLite `datetime('now','+8 hours')`（UTC+8），比對卻用 `datetime.now()`（UTC 主機）→ 恆差 8 小時 → 節流永不觸發
**影響**：1s poll 下 prices 表每秒插一列，DB 無限膨脹
**修復**：統一 UTC 基準

### M-3. 無認證 + 0.0.0.0 + CORS * + 可寫策略檔 → LAN 遠端程式碼執行
**修補狀態**：⏸ **Decision Pending**（spec D20，待使用者拍板 a/b/c/d）— 2026-08-09 實測：`Access-Control-Allow-Origin: *` 仍在（HTTP header 確認）、監聽 `0.0.0.0:8899`、simulate 無認證可寫入；未做任何變更

**檔案**：`quant_fleet_server.py` 第 1164 行
**問題**：所有端點無認證、綁 0.0.0.0、`Access-Control-Allow-Origin: *`；`/api/strategy/save` 可寫任意 .js（每 poll 被 node eval 執行）；`/api/reset` 可清空帳戶、`/api/trade/simulate` 可偽造交易（子代理實測 TESTFAKE 開倉成功）
**影響**：同網段任何機器/瀏覽器可操控帳戶、偽造交易、**寫入 JS 以伺服器身分執行（RCE）**
**修復**：至少限制寫入型端點僅 localhost / 加 token 認證 / 移除 CORS *

### M-4. HTTPServer 單執行緒 — 慢請求阻塞全站
**修補狀態**：⏳ **未修補** — line 1164 仍 `http.server.HTTPServer`（非 Threading）；spec D2 已決策方案 C（ThreadingHTTPServer + threading.local get_db()），未執行

**檔案**：`quant_fleet_server.py` 第 1164 行
**問題**：非 ThreadingHTTPServer；fetch_all_data 含同步網路呼叫（timeout 10s×3）+ node 子程序（15s）+ backtest 循序（120s×N）
**影響**：任一慢請求卡住 → /api/data、帳戶頁、靜態檔全部凍結
**修復**：改 ThreadingHTTPServer（db_lock/log_lock 已就緒）

### M-5. /api/trade/simulate 無輸入驗證
**修補狀態**：✅ **已修補**（2026-08-09）— tests/test_http.py SimulateValidationTests 5/5 綠：symbol 不在 watchlist → 400、side ∉ {BUY,SELL} → 400、price ≤ 0/非數字 → 400（原本 NOPEUSDT 可開倉、HOLD 落入 else 開空）；合法 simulate 仍 200

**檔案**：`quant_fleet_server.py` 第 1038 行
**問題**：symbol 不檢查 watchlist（TESTFAKE 可開倉）、price 任意、side 任意值落入 else 開空
**影響**：任何人可注入假交易污染帳戶/統計/權益曲線
**修復**：驗證 symbol ∈ watchlist、side ∈ {BUY,SELL}、price>0；或移除端點

### M-6. 儲存型 XSS 家族（前端）
**修補狀態**：✅ **已修補**（2026-08-09，spec D18）— 前端 tests/test_frontend.js 30/30 綠（esc() 補轉 `"`/`'`；loadStrategyList/renderBacktestResults/loadSymbols（含 data-sym 屬性）/renderRecentTrades/renderPositionsBar/renderSignalTable confidence/logLine 參數/帳戶表格等 22 個內插點全數 esc）；server 端 /api/symbols/add 加 charset 白名單（tests/test_http.py SymbolValidationTests 6/6：HTML/引號 payload → 400、合法 symbol/中文名 → 200）

**檔案**：`dashboard/js/app.js` 第 66/169/751 行
**問題**：策略 NAME/DESCRIPTION（loadStrategyList）、回測策略名（renderBacktestResults）、watchlist symbol/name（loadSymbols，`data-sym` 屬性可跳出）直接拼 innerHTML；`esc()` 只轉 `& < >` 不轉引號；server 端 watchlist name 完全無驗證
**影響**：watchlist/策略頁開啟即執行攻擊者腳本（LAN 內可達）
**修復**：esc() 補轉 `" '`；所有內插值過 esc()；server 端 name 白名單

### M-7. I18n.init() 無 .catch — i18n 載入失敗整機死機
**修補狀態**：✅ **已修補**（2026-08-09，spec D19）— tests/test_frontend.js D19 行為測試 2/2 綠：vm stub fetch 全失敗下 `I18n.init()` 仍 resolve（boot 繼續、T() 回退英文/key 名）；boot call site 已加 .catch 兜底；init 失敗後 15s 自動重試語言檔

**檔案**：`dashboard/js/app.js` 第 783 行
**問題**：init().then 無 catch；任一語言檔 fetch 失敗 → fetchData 永不啟動、永久 Initializing
**影響**：儀表板無恢復路徑
**修復**：加 .catch 以英文啟動 + 重試

### M-8. backtest 引擎不支援 add / close_pct / size_pct
**修補狀態**：⏳ **未修補** — strategies/_run_backtest.js 無 add/close_pct/size_pct 處理（grep 零命中）；spec D6 已規劃與 execute_trade 對齊，未執行

**檔案**：`strategies/_run_backtest.js` 第 128 行
**問題**：add 無效（持倉時 BUY no-op）、close_pct 無效（一律全平）、size_pct 無效（固定 5%）
**影響**：grid.js 回測退化成「跌 0.1% 開倉、回中心全平」的 churn 機器，**回測與 live 嚴重脫節**（違背 README「same evaluate() path」宣稱）
**修復**：backtestOne 實作 add/close_pct/size_pct 與 execute_trade 對齊

### M-9. grid 狀態遺失後 recover 整倉全平
**修補狀態**：⏳ **未修補** — strategies/grid.js line 43 recover 分支仍 `levels: 1`（未依 position.quantity 反推）；spec 無對應 D 項（D6 覆蓋部分語義），需補

**檔案**：`strategies/grid.js` 第 41 行
**問題**：server 重啟/存檔清 _strategy_state → recover 分支設 levels=1 → 價格一回 center 即 close_pct=1/1 把累積多層倉位一次倒光
**影響**：任何重啟/編輯策略後已持網格倉位被整筆出場
**修復**：recover 依 position.quantity 反推 levels；至少避免 close_pct=1.0 全平路徑

### M-10. active_strategy 指向已刪檔案 + node 失敗靜默 + save 無語法檢查
**修補狀態**：⏳ **未修補** — line 48 `active_strategy = "default.js"` 仍指向不存在的檔案（無啟動存在性檢查）；/api/strategy/save（line 1081）仍無 `node --check`；spec D17 已規劃，未執行

**檔案**：`quant_fleet_server.py` 第 48/113/1081 行
**問題**：啟動預設 default.js 無存在性檢查（ENOENT 靜默回 {}）；run_js_strategy 失敗全靜默；/save 不跑 node --check
**影響**：策略壞掉時平台靜默停擺，無任何錯誤回饋
**修復**：啟動檢查檔案存在；save 前 node --check；失敗寫 exec_log 警告

---

## 🟡 Minor（程式碼品質 / 邊界 / 效能）

### N-1. cover short 現金不足時靜默部分回補
**修補狀態**：⏳ 未修補 — line 280 仍 `qty = min(pos[1]*close_pct, cash/price)` 靜默部分成交；spec D9 已規劃回 rejected，未執行

`quant_fleet_server.py` 第 280 行 — `qty = min(pos[1]*close_pct, cash/price)` 現金不足靜默部分成交且回 filled，無 notional 下限檢查 → grid 狀態與實際倉位漂移。應回 rejected 或明確回報成交 qty。

### N-2. Binance fetch 快取失敗不回退 stale 資料
**修補狀態**：⏳ 未修補 — line 233 失敗仍 `return data`（None），無 stale 回退；spec D10 已規劃，未執行

`quant_fleet_server.py` 第 233 行 — 請求失敗回 None → 整頁 502 或 klines=None 使指標退化（sma=0/macd=0 → 策略誤判）。應回傳過期快取。

### N-3. bookTicker 每 poll 抓取無快取
**修補狀態**：⏳ 未修補 — line 620 仍每 poll 直接 `fetch_json(bookTicker)`；spec D11 已規劃 2-5s TTL，未執行

`quant_fleet_server.py` 第 620 行 — weight≈4/call × 1s poll ≈ 240/min；多 tab 逼近 1200 上限。加 2-5s TTL。

### N-4. signals 表唯寫且無限增長
**修補狀態**：⏳ 未修補 — signals 表仍逐 poll 寫入無清理；spec D12 已規劃 WAIT 不寫 + 容量上限，未執行

`quant_fleet_server.py` 第 726 行 — 1s poll × 7 symbols ≈ 60 萬列/日，全檔只有 INSERT 無 SELECT。WAIT 不寫或定期清理。

### N-5. _strategy_state 未在 activate 時清除
**修補狀態**：⏳ 未修補 — activate（line 1030）仍只 `_last_signal.clear()`，無 `_strategy_state.pop`；spec D13 已規劃，未執行

`quant_fleet_server.py` 第 1030 行 — re-activate 帶回舊 state（grid 舊 idx 語意污染新邏輯）。activate 一併 pop。

### N-6. /api/portfolio 與 /api/data 權益計算不一致
**修補狀態**：⏳ 未修補 — /api/portfolio（line 925）與 /api/data 權益計算仍不一致；spec D14 已規劃統一函式，未執行

`quant_fleet_server.py` 第 925 行 — portfolio 用 re-mark 價、data 用即時價；current_price=0 時退化 entry。統一函式。

### N-7. 每秒全表 replay（效能）
**修補狀態**：⏳ 未修補 — line 883 仍每 poll 全表 replay portfolio_stats；spec D15 已規劃增量維護，未執行

`quant_fleet_server.py` 第 883 行 — portfolio_stats/equity_curve/rebuild_cycles 每次 poll 全表重播，O(n)/O(n²)；grid 高頻交易下 trades 快速累積。增量維護或緩存。

### N-8. 死碼：_esc / add_log / global pause 殘留
**修補狀態**：⏳ 未修補 — `_esc`（line 17）、`add_log`（line 52）、`global _trading_paused_until`（line 1043）仍在；spec D16 已規劃刪除，未執行

`quant_fleet_server.py` 第 17/52/1043 行 — `_esc` 無呼叫點、`add_log` 無呼叫者、`global _trading_paused_until` 指向未定義名字。刪除。

### N-9. fetch_json 裸 except 吞錯 + log_message 靜默
**修補狀態**：⏳ 未修補 — line 216 `fetch_json` 仍裸 `except: return None`；spec D17 已規劃寫 exec_log/stderr，未執行

`quant_fleet_server.py` 第 216 行 — Binance 封鎖/node 錯誤/DB 異常全無跡可尋。失敗寫 stderr/exec_log。

### N-10. 做空無曝險限制
**修補狀態**：⏳ 未修補 — 做空無曝險限制（文件標註亦未做）；spec D21 已規劃，未執行

`quant_fleet_server.py` 第 323 行 — cash 隨做空增長可無限疊加空倉。限制總曝險或文件標註。

### N-11. positions 儲存未 round（與 trades 漂移）
**修補狀態**：⏳ 未修補 — line 308 positions 仍存原始浮點（trades 有 round）；spec D21 已規劃，未執行

`quant_fleet_server.py` 第 308 行 — trades round(qty,8) 但 positions 原始浮點，加倉/部分平倉後微漂移。

### N-12. `_sma4h` 欄位標籤誤導
**修補狀態**：⏳ 未修補 — line 829 `_sma4h` 仍取 `indicators["sma20"]`（1h 誤標 4h）；spec D21 已規劃，未執行

`quant_fleet_server.py` 第 829 行 — 實際為 1h SMA20 卻命名 sma4h。改傳 indicators['sma_4h'] 或改名。

### N-13. strategy_matrix 硬編碼第一個策略為 active
**修補狀態**：⏳ 未修補 — line 863 仍 `active = (si == 0)` 硬編碼；spec D21 已規劃，未執行

`quant_fleet_server.py` 第 863 行 — `si==0` 與實際 active_strategy 無關，展示造假。

### N-14. 策略 NAME/DESCRIPTION regex 僅支援雙引號
**修補狀態**：⏳ 未修補 — line 71 regex 仍僅 `\"([^\"]+)\"`；spec D21 已規劃改 `['\"]`，未執行

`quant_fleet_server.py` 第 71 行 — 單引號字串的策略 meta 提取失敗。regex 改 `['"]`。

### N-15. 前端多處內插值未逸出（渲染層）
**修補狀態**：✅ **已修補**（2026-08-09，spec D18）— 同上：286/419/432/445 行內插值已 esc；400 行 pipeline 節點改 index（n===0/n===7）比對，zh 標籤失效問題一併解除

`dashboard/js/app.js` 第 286/400/419/432/445 行 — renderRecentTrades 的 symbol/side、renderPositionsBar、renderSignalTable confidence、logLine 參數未過 esc()；pipeline 節點以 label 比對 SIGNAL/DONE 切 zh 後失效（改用 index）。

### N-16. WS 雙 stream 重連互相拖累、固定 5s 無退避
**修補狀態**：⏳ 未修補 — app.js line 476 WS 雙 stream 仍互相拖累、固定 5s 重連；spec D21 已規劃獨立重連+退避，未執行

`dashboard/js/app.js` 第 476 行 — 任一 stream 斷線關閉另一條健康連線。各自獨立重連 + 指數退避。

### N-17. 客戶端熱評估訊號與 server 執行訊號不一致
**修補狀態**：⏳ 未修補 — app.js line 518 客戶端熱評估覆寫 signal 仍在；spec D21 已規劃標示預覽，未執行

`dashboard/js/app.js` 第 518 行 — 前端用 WS 即時價 + buffer 重算 signal 覆寫 UI，server 用 klines 下單 → 顯示與成交可能矛盾。以 server 為準或標示預覽。

### N-18. updateRailPrices 依賴 span 順序猜價格格
**修補狀態**：⏳ 未修補 — app.js line 514 仍 `spans[spans.length-2]`；spec D21 已規劃 data-price，未執行

`dashboard/js/app.js` 第 514 行 — `spans[spans.length-2]` 脆弱。加 data-price 屬性。

### N-19. loadAccount 無錯誤處理 → 白屏
**修補狀態**：⏳ 未修補 — app.js line 622 loadAccount 仍無 .catch；spec D21 已規劃，未執行

`dashboard/js/app.js` 第 622 行 — 非 200 回應即 TypeError。加 .catch + 欄位防呆。

### N-20. 多處硬編碼英文未走 i18n
**修補狀態**：⏳ 未修補 — app.js line 765 + HTML 硬編碼英文仍在；spec D21 已規劃，未執行

`dashboard/js/app.js` 第 765 行 + HTML — resetAccount confirm、statusBar Initializing、textarea/input placeholders、頂欄 // WATCHLIST（key 存在未引用）。

### N-21. OBI 加倉 cooldown 依賴牆鐘 Date.now()
**修補狀態**：➖ **不適用** — OBI.js 已刪除（C-3 設計決策），無此檔案可修

`strategies/OBI.js`（82ef484^ 版本）第 61 行 — backtest 中永不過期、server 重啟後遺失。以 bar 序號取代。

### N-22. _run_strategy.js 未驗證策略檔名（縱深防禦缺失）
**修補狀態**：⏳ 未修補 — strategies/_run_strategy.js line 18 仍無檔名驗證；spec D21 已規劃，未執行

`strategies/_run_strategy.js` 第 18 行 — helper 應自行檢查 `^[A-Za-z0-9_-]+\.js$`。

### N-23. 策略 state 版本相容（改檔不清 state）
**修補狀態**：⏳ 未修補 — state 仍無 mtime/hash 版本判斷；spec D21 已規劃，未執行

`strategies/_run_strategy.js` 第 22 行 — git pull/手動編輯改檔不清 state → 舊語義污染新 code。以 mtime/hash 判斷。

### N-24. grid.js 死狀態 g.last（只寫不讀）
**修補狀態**：⏳ 未修補 — grid.js line 23 仍寫死狀態 `g.last`；spec D21 已規劃，未執行

`strategies/grid.js` 第 23 行 — 移除或實作原意。

### N-25. backtest ticker 與 live 語義差異
**修補狀態**：⏳ 未修補 — _run_backtest.js line 111 仍 `change_pct: 0`、無 book；spec D21 已規劃 README 註明差異，未執行

`strategies/_run_backtest.js` 第 111 行 — change_pct 恆 0、portfolio.total_equity 範圍不同、volSurge 閾值不同。README 註明差異清單。

### N-26. init_db.py existing>300 跳過下載，缺檔不補
**修補狀態**：⏳ 未修補 — init_db.py line 92 仍 `existing > 300` 跳過；spec D4 已規劃以最大日期判斷，未執行

`init_db.py` 第 92 行 — 新上架幣種/失敗月份永不補抓。以最大日期判斷完整性。

### N-27. README 過時
**修補狀態**：⏳ 未修補 — README line 60/188 仍列 default.js 為內建（C-3 配套）；spec D3 已規劃移除+補 grid.js 文件，未執行

`README.md` 第 60/135/186 行 — 仍列 default.js 為內建（已刪）、未列 grid.js、漏 size_pct 文件。

---

## 🔵 Informational（文件 / 架構 / 建議）

- **I-1** 無 CSP + Tailwind CDN — 加 CSP；tailwind 本機打包
  - **修補狀態**：⏳ 未修補（spec Out of Scope：前端重構/CSP/Tailwind 本地打包另案）— HTML line 7 仍用 CDN、無 CSP
- **I-2** 25 個死 i18n key（en/zh 對齊但未引用）— 刪除或接回硬編碼處
  - **修補狀態**：⏳ 未修補 — 25 個死 i18n key 仍在（en/zh 對齊）；spec D21 未列明細，需補
- **I-3** `<html lang="en">` 固定、title 未地化 — setLang 同步
  - **修補狀態**：⏳ 未修補 — `<html lang="en">` 固定、title 未地化；spec D21 未列明細，需補
- **I-4** saveStrategy 的 `replace(/\n/g,'\n')` no-op 殘留碼 — 移除
  - **修補狀態**：⏳ 未修補 — app.js line 127/548 no-op `replace(/\n/g)` 仍在；spec D16 已規劃刪除，未執行
- **I-5** hasOwnProperty 合併靜默丟 server 新欄位 — 明確欄位映射
  - **修補狀態**：⏳ 未修補 — app.js line 599 hasOwnProperty 合併仍在；spec D21 未列明細，需補
- **I-6** loadActiveJSStrategy 開機重複 fetch（loadStrategies 內已呼叫）— 移除其一
  - **修補狀態**：⏳ 未修補 — loadActiveJSStrategy 開機仍 fetch 兩次（line 783）；spec D21 未列明細，需補
- **I-7** radar 動畫在非 dashboard 頁背景持續跑 — 比照 pipeline 加 currentPage 檢查
  - **修補狀態**：⏳ 未修補 — app.js line 299 radar 動畫仍無 currentPage 檢查；spec D21 未列明細，需補
- **I-8** I18n.t 參數 `${cap}` 約定脆弱 — 統一 `{cap}` + 參數 esc()
  - **修補狀態**：⏳ 未修補 — `I18n.t` `${cap}` 約定仍在（app.js line 6）；spec D21 未列明細，需補
- **I-9** RSI 邊界 flip-flop（default.js）— 平多後 RSI>70 立即開空，無 cooldown
  - **修補狀態**：➖ 不適用 — default.js 已刪除（C-3 設計決策），無此檔案可修
- **I-10** ppmb 純動能高 churn（DESCRIPTION 未註明）— 加 position 檢查或明示
  - **修補狀態**：➖ 不適用 — ppmb.js 已刪除（C-3 設計決策），無此檔案可修
- **I-11** 做空無保證金/強平（紙交易簡化）— 文件標註為設計
  - **修補狀態**：✅ 設計維持 — 紙交易無保證金/強平為既有設計，文件標註即可
- **I-12** `_esc` 保留但無用 — 與 N-8 同
  - **修補狀態**：⏳ 未修補 — 同 N-8（`_esc` 仍在）；spec D16 已規劃，未執行
- **I-13** exec_log/DB/kline 三處時間基準混用（UTC / UTC+8 / UTC 日期）— 統一 UTC
  - **修補狀態**：⏳ 未修補 — 三處時間基準混用仍在（line 290/305/321/336/349/608 皆 `datetime('now','+8 hours')`）；spec D8 只涵蓋 prices 節流最小範圍，完整統一 Out of Scope

---

## 總結

| 嚴重度 | 數量 | 關鍵項目 | 修補狀態（2026-08-09） |
|--------|------|----------|------------------------|
| 🔴 Critical | 5 | 路徑穿越×2（DB 洩漏）、策略檔誤刪、1970 日期、空頭清算符號 | ⏳ 4 未修（實測 DB 仍可下載）+ 🚫 C-3 決策不恢復（配套未完成） |
| 🟠 Major | 10 | size_pct 遺漏、時區節流失效、無認證 RCE、單執行緒、simulate、XSS、i18n 死機、backtest 語義、grid 全平、靜默故障 | ⏳ 9 未修 + ⏸ M-3 待決策 |
| 🟡 Minor | 27 | 上述 N-1 ~ N-27 | ⏳ 24 未修 + ➖ 3 不適用（N-21 等，檔案已刪） |
| 🔵 Info | 13 | 上述 I-1 ~ I-13 | ⏳ 9 未修 + ➖ 2 不適用 + ✅ 1 設計維持 + ⏳ 1 同 N-8 |

**建議修復順序（依 `spec/remediation.md` D1 → D21）**：
1. **第一批（安全）**：D1 路徑穿越（C-1/C-2，實測仍可下載 DB）→ D18 XSS（M-6/N-15）→ D19 i18n 容錯（M-7）→ M-5 simulate 驗證（實測無認證開倉成功）
2. **第二批（資料正確性）**：D4 init_db 時間戳（C-4/N-26）→ D5 空頭清算（C-5）→ D6/D7 backtest 語義（M-8/M-1）
3. **第三批（可靠度）**：D2 Threading（M-4）→ D17 錯誤可見性（M-10/N-9）→ D8 prices 節流（M-2）→ D3 配套（C-3 剩餘：active_strategy="" + README）
4. **第四批（Minor 批次）**：D9 ~ D16、D21（N-1 ~ N-20、N-22 ~ N-27、I 系列）
5. **M-3 認證**：待使用者拍板（spec D20 a/b/c/d）後補入

> 驗證完成（2026-08-09）：55 項中 0 項已修補；spec/remediation.md 已就緒，修補待依上述批次執行，每批完成後在本文件標記 ✅。
