# Ledger 歷史

> LEDGER.md 只保留最近兩輪的盤點發現；更早的依時間由新到舊附在這裡（README §6.4）。

## 前次盤點發現（2026-11-27 18:55 第 4 輪指令 7，`snapshots/2026-11-27.json`）

- **和第 3 輪比**：只有 `ohlcv_daily` +6（Q011 重爬當月）。I023 上游仍截斷。
- **第 3 輪提交**：`944bd98`，PR #82 8 個 check 全過，沒有補救。
- **挑題**：執行 Q012（可跑的 draft 中最舊的）。補爬 MOPS 29 列全部 found；C1、C2 對公司公布的 EPS 9＋8 列全部一致。跳過 Q005、Q007、Q010（同前）、Q013（I023 上游）、Q014（依賴 I024，C5 必定 FAIL）。
- **出題（指令 2）**：可跑的 draft 為 0。補 1 題 **Q015**（籌碼）：外資持股的股數／比率／上限三欄內部一致（上游直接給的欄位，算錯值防線）。出題時發現 TWSE 的持股比率是**捨去**不是四捨五入（全表 35／88 列用四捨五入會誤判），以及 2004 年 1103 有 6 列上游把上限寫成 0.00（L0，已寫進 C3）。
- **README**：指令 7 出題每輪最多 2 題（被 issue 擋住的 draft 會越積越多）。
- **建議修復順序**：同第 3 輪。I024 方案 B（0 次上游呼叫）修好後 Q014 就能跑。

## 前次盤點發現（2026-11-27 18:40 第 3 輪指令 7，`snapshots/2026-11-27.json`）

- **和 18:29（第 2 輪）比**：沒有任何變化（列數、usable%、剖析都一樣）。
- **第 2 輪提交**：`c1606a3`，PR #82 第一次 `test-live-sql` 失敗：`poc.raw_market_valuation_summary` 45 秒逾時（0 bytes），同一段 log 還有 `/quarterly-financials`、`/financial-analysis` 的 504，屬上游環境（本 commit 只改 `verify_questions/`）。依指令 8 `gh run rerun 45983338681 --failed` 重跑一次，8 個 check 全過。
- **I023 上游仍未恢復**：MOPS 115/09 頁 18:40 仍是 133,276 bytes、284 家。Q013 的 TWSE 補爬繼續延後。
- **挑題**：執行 Q011（可跑的 draft 中最舊的）。依 §8.3 規則 5 先 `crawl_delete_rows` 當月 3 列（pin 3、0 次上游）再重爬（3 次 API），日 K 最新日從 11-25 變成 11-27。跳過 Q005、Q007、Q010（同前）、Q013（等 I023 上游）。
- **出題（指令 2）**：可跑的 draft 剩 Q012、Q013，補 1 題 **Q014**（股利，第二個 `accuracy`）：golden 取自 TWSE 除權除息計算結果表（27 個事件），FinMind 與 yfinance 都分毫不差（調整後配息率，例：2317 2026 公告 7.2 元、實際 7.171792 元）。C5 抓到 I024。
- **新 issue**：I024（S3＋S4）FinMind 股利把增資／減資除權當成金額全 0 的股利事件；`dividend` 只有 1 列，但 `raw_dividend_history` 裡還有 331 個（6.1%），cron 輪到時會白打 331 次 FinMind。
- **建議修復順序（§7.4）**：I023（S1，等上游）→ I019 → I022 → I020 → I016（S2）→ I024（S3＋S4，方案 B 0 次上游呼叫，成本最低）→ I021、I018（S3，待使用者決定）。

## 前次盤點發現（2026-11-27 18:29 第 2 輪指令 7，`snapshots/2026-11-27.json`）

- **和 18:15（第 1 輪）比**：列數變化全部來自第 1 輪的 Q008 補爬；§7.5 剖析報出 `monthly_revenue_twse` 3 組新的實體重複，就是 I023（剖析能抓到這個失效模式，確認有報警）。24 小時內 API 請求 44 次、5xx 0 次。
- **I023 上游還沒恢復**：`upstream_peek.py t21sc03 sii:115:9` 18:29 仍是 133,276 bytes、284 家、Last-Modified 09:59:23 GMT。
- **第 1 輪提交**：`2f54762`，[PR #82](https://github.com/jeffrey82221/twstock_api/pull/82) 8 個 check 全過（test-db-demo、endpoint、live-sql、pg-tool、pipeline、source、sql、upstream），沒有補救。
- **挑題**：執行 Q009（最舊的可跑 draft）。跳過 Q005、Q007、Q010（理由同第 1 輪）。
- **工具**：`crawl_card.py` 新增 `since`：`crawl_insert_filtered` 的 filters 只有 = 與 IN，Q009 的 360 列 pending 全在窗口外，照 DESC 補會白爬 6 列。Q008、Q009 的 crawl 步驟補上 `since`。
- **LEDGER 結構**：「前次盤點發現」只留最近一輪，更早的搬到 [`LEDGER_HISTORY.md`](LEDGER_HISTORY.md)（README §6.4）。
- **出題（指令 2）**：draft 剩 Q007、Q010、Q011，但 Q007、Q010 被 issue 擋住，可跑的只有 Q011。指令 7 原文「draft 少於 3 題」沒扣掉被擋住的題，改成「可跑的 draft 少於 3 題」（README 指令 7），本輪補 2 題：
  - **Q012**（體質）2330、2317、2454 年度 EPS 連續成長：**第一個 `accuracy` 斷言**，golden 值 8 筆（年報、法說會），C1 年度、C2 四季合計，加完整、唯一、精確。預估補爬 MOPS 29 列。2330 2025 的 66.25（法說稀釋 EPS）vs DB 66.26 定義不同，先不放。
  - **Q013**（成長）2330、2317、1402 月營收年增率：`monthly_revenue`（FinMind）第一次有斷言（完整、唯一、及時、一致、精確），預估補爬 FinMind 17、TWSE 9 列。**補爬前要先確認 MOPS 115/09 頁恢復**（2317 在截斷點之後，I023）。
  - 兩題的 SQL 都已執行過；C1／C2／C3 的 FAIL 是還沒補爬的列，已有的 2330 列全部通過。
- **建議修復順序**：同第 1 輪（I023 → I019 → I022 → I020 → I016 → I021、I018）。

## 前次盤點發現（2026-11-27 18:15 第 1 輪指令 7，`snapshots/2026-11-27.json`）

- **和 00:04 那份比**：只有 `sbl_history` −296 列、跨 as_of 重複 302 → 0（I017 修復）；沒有新的全空欄位，raw 平均 usable 96.0% 不變，24 小時內 API 請求 9 次、5xx 0 次。
- **工具修正**：`snapshot.py` 同一天重跑時原本和前一天的 snapshot 比，會把今天稍早的變化重報一次，也看不到這一輪的變化；改成一律和最近一份比（讀進記憶體後才覆寫）。`retry_failed.py` 的 `raw_dividend_history_yfinance` seed 還寫 `company_list`（I015 前的上游），`--apply` 會刪錯 seed；改成 `company_basic_info_list`。
- **挑題**：執行 Q008（最舊的 draft）。跳過 Q005（只剩使用者決定保留的 C1）、Q007（依賴 I016）、Q010（依賴 I019、I020，C2、C3 必定 FAIL）。Q009、Q011 留給下一輪。
- **新 issue**：I022（S2，L1）產品營收負數列被丟掉；I023（S1，L0＋L1）MOPS 9 月月營收頁在台北 17:59 被截斷，adapter 當成完整頁，本輪補爬的 2308、2882、3045 有 6 列缺 9 月。I023 是從 Q008 C3 的可比對列數 12 < 預期 15 追到的。
- **出題**：draft 有 Q007、Q009、Q010、Q011 四題，不需要補。
- **建議修復順序（§7.4）**：I023（S1 完整性；先確認上游恢復）→ I019（S2 唯一性／及時性）→ I022（S2 完整性）→ I020（S2 一致性，假說待確認）→ I016（S2 完整性，方案待決定）→ I021、I018（S3，待使用者決定）。

## 前次盤點發現（2026-11-27 00:04 指令 7，第一份含 §7.5 剖析）

- **raw 平均 usable 96.0%**，與前次相同；只多了 Q005 補爬的 9 個股利事件。
- **第一次維度剖析**：13 個全空欄位（8 個 I016、5 個結構性），4 組實體重複（2 個缺陷 I017、I018，2 個合法），2 組部分空值待查（法人 27 列、月營收 YoY）。分類見「剖析基線」。
- **新 issue（全部 diagnosed）**：I017 借券視窗重疊（S2）、I018 新聞重複與單日覆蓋（S3，待決定）、I019 金融股 Q3 凍結成上一季（S2）、I020 2882 TWSE 隱含 EPS 與 MOPS 差 6–7%（S2，假說待確認）、I021 日 K 當月凍結（S3，待決定）。
- **維度矩陣的空格**：準確一欄全空；精確、合理只有 Q011／Q009 的 draft 試跑；唯一性正式斷言只有 Q008 C5（draft）。
- **挑題**：執行 Q006（最舊的 draft）。跳過 Q005（failing，但唯一的 FAIL C1 是使用者決定保留的）與 Q007（依賴未修的 I016，且使用者在 `app/service.py` 有進行中的修改）。
- **提交**：`93db519`，[PR #81](https://github.com/jeffrey82221/twstock_api/pull/81) 8 個 check 全過（test-db-demo、endpoint、live-sql、pg-tool、pipeline、source、sql、upstream），沒有補救。
- **提交（續）**：`2567b60`（補記 CI）PR #81 8 個 check 全過。
- **I017 修復（指令 5）**：`sbl_history` 27,058 → 26,762 列，跨 as_of 重複 302 → 0，0 次上游呼叫；Q009 6／6、Q004 5／5 PASS。
- **環境**：MCP `twstock-db` 連線逾時，改用 `server.py` 函式；API 原本沒有啟動。DB 時區是 UTC，台北時間 00:00–08:00 的 `CURRENT_DATE` 是前一天（已寫進 README §7.2）。

## 前次盤點發現（2026-11-26 指令 7）

- **raw 平均 usable 96.0%**，與 I015 後相同；三個最低的 raw 都是已知的合法無資料。
- **新現象 1（→ I016）**：`financial_quarterly_yfinance`／`financial_yearly_yfinance` 共 1,331 列的 `eps_ttm`、`revenue_ttm`、`net_income_ttm`、`operating_margin_pct` 全部 NULL，raw 卻是 `found=true`。usable% 只看 found，看不出這種「成功但全空」。
- **新現象 2（→ Q008 待分流）**：`product_revenue` 166 組（stk_code, ym）中有 16 組產品金額或占比加總對不上（金融業最多，2891 占比合計 449%）。
- **新現象 3**：`company_list` 1,057／2,250（47%），不少上市公司（例：1402 遠東新、1519 華城）還沒進母體；不是品質問題，是還沒爬。
- **沒被任何問題覆蓋的 pop 表**：`monthly_revenue`（FinMind）、`raw_yearly_financials`、`finmind_news`。

## 前次盤點發現（2026-11-26 重建後）

依使用者要求，照 repo README 刪除並重建 `poc`、`pop`、`hidden` 三個 schema（README §10.6），再還原 seed、跑 `retry_failed.py`：

- **usable% 變化（對 2026-11-25）**：`raw_company_valuation` 89.5 → 99.8、`raw_monthly_revenue_twse` 75.1 → 100、`raw_sbl_history` 40.8 → 77.0、`raw_financial_analysis` 88.3 → 94.8、`raw_yearly_financials` 83.0 → 100、`raw_dividend_history` 90.4 → 100、`raw_dividend` 95.1 → 100、`raw_institutional_net_buy_sell` 100 → 100（533 列，假日回溯後取樣日不同）。
- **剩下的非可用列多為正當的 404**：`raw_sbl_history` 85 列（該月無借券）、`raw_finmind_news` 26 列（2330 2019–2024 部分月份 FinMind 無新聞）、`raw_financial_analysis` 15 列。`raw_product_revenue` 剩 1 列為 2637（KY）免申報的合法無資料（I013），`raw_mops_quarterly_financials` 1 列是 8463 上市前。
- **重建後少掉的列**：yfinance 年／季財報約 300／270 列、FinMind 股利事件 5 筆、借券 2 個月。這些 key 已不在目前 seed SQL 的產生範圍內（不是遺失）。
- **新發現**：I011（raw／正規化分歧）、I012（FinMind 股利 end_date 截掉當次事件）。兩者都已修復。
- 備份：`data/backup/pop_hidden_20261125.dump`、`data/backup/pop_seed_lists_20261125.sql.gz`、DB schema `seed_backup_20261125`（23 張 seed，17,332 列；確認無誤後可 DROP）。

## 前次盤點發現（2026-11-25，`snapshots/2026-11-25.json`）

第一份 snapshot，沒有前一份可比；與 README §12（2026-11-24 手動觀察）相比：

- **usable% 變化**：`raw_sbl_history` 40.5 → 40.8（指令 0 重爬 1 列）；其他不變。cron 全部未排程、24 小時內只有 2 次真實 HTTP 請求，資料庫處於靜止狀態。
- **新現象 1：上櫃股完全沒有估值資料**。`raw_company_valuation` 的 168 列 NULL 全是上櫃股（25 檔），且上櫃股可用列數 = 0。`/api/company-valuation` 的上游 TWSE BWIBBU_d 只涵蓋上市。
- **新現象 2：`raw_financial_analysis` 2026 年 19／19 列 NULL**。年報要隔年才公布，seed 卻產生了當年度，每列都是必定作廢的呼叫。另有 2012–2020 年零星 NULL（每年 1–3 檔）。
- **新現象 3：`raw_finmind_news` 的 26 列 NULL 有 24 列是同一檔股票的 2019–2020 全年**，其餘 2 列零星。
- **沒被任何問題覆蓋的 pop 表**：全部 20 張（尚未出題）。

