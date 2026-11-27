# Ledger 歷史

> LEDGER.md 只保留最近兩輪的盤點發現；更早的依時間由新到舊附在這裡（README §6.4）。

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

