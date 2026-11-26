# Ledger

> 由 AI 指令維護（見 [README §6.4](README.md#64-ledgermd)）。手動修改也可以，AI 會以這裡的內容為準。

## 問題

| ID | 策略 | 標題 | 狀態 | 最後執行 | 未結 issue |
| --- | --- | --- | --- | --- | --- |
| [Q001](questions/Q001-valuation-per-percentile.md) | 估值 | 2330、2317、6488 本益比在近 3 年第幾百分位 | passing | 2026-11-26 | — |
| [Q002](questions/Q002-growth-revenue-momentum.md) | 成長 | 2330、2454、6488 月營收 YoY 連 3 月 > 20%，季營收對得起來 | passing | 2026-11-26 | — |
| [Q003](questions/Q003-quality-roe-debt.md) | 體質 | 2330、2882、1101 連 3 年 ROE > 15% 且負債比 < 50% | passing | 2026-11-26 | — |
| [Q004](questions/Q004-flow-foreign-institutional.md) | 籌碼 | 2330、2317、6488 外資持股、法人買賣超、借券趨勢 | passing | 2026-11-26 | — |
| [Q005](questions/Q005-income-dividend-consistency.md) | 股利 | 2330、2317、2882、1101 連 5 年配息與殖利率 | failing（C1 刻意保留） | 2026-11-26 | I003（使用者決定保留 FAIL 作提醒） |

## Issues

| ID | 嚴重度 | 層級 | 標題 | 狀態 | 發現於 | 修復 commit |
| --- | --- | --- | --- | --- | --- | --- |
| [I001](issues/I001-mops-quarterly-financials-ytd.md) | S1 | L1 | mops_quarterly_financials 營收與 EPS 為年初至今累計 | closed | Q002, Q003 | 指令 5 |
| [I002](issues/I002-point-in-time-look-ahead.md) | S1 | L2／L3 設計 | as_of 看得到尚未公布的月營收與季報 | closed（選項 B：as_of＝已公布＋`available_date`） | Q002 | 2026-11-26 |
| [I003](issues/I003-yfinance-dividend-adjusted.md) | S1 | L1＋L0 | dividend_yfinance 現金股利為配股還原值 | closed（2882 L0 殘留，Q005 C1 保留 FAIL 作提醒） | Q005 | 指令 5 |
| [I004](issues/I004-normalized-as-of-from-response.md) | S2 | L5 | 正規化 view 的 as_of 取自回應，失敗列主鍵為 NULL | closed（第二批 8 張以 pin 重建，0 次 HTTP） | Q001 | 指令 5、2026-11-26 |
| [I005](issues/I005-otc-valuation-missing.md) | S2 | L1 新上游 | 上櫃股沒有估值資料 | closed（TPEx peQryDate，2007-01-02 起） | Q001 | 2026-11-26 |
| [I006](issues/I006-otc-foreign-ownership-missing.md) | S2 | L1 新上游 | 上櫃股沒有外資持股資料 | closed（TPEx qfii，2007-05-02 起） | Q004 | 2026-11-26 |
| [I007](issues/I007-institutional-weekend-no-fallback.md) | S2 | L1 | 法人買賣超取樣日遇假日不回溯 | closed | Q004 | 指令 5、2026-11-26 |
| [I008](issues/I008-financial-analysis-current-year.md) | S4 | L3 | financial_analysis_year_list 產生當年度 | closed | Q003 | 指令 5 |
| [I009](issues/I009-mops-quarterly-financial-companies.md) | S2 | L1 | 金融股沒有季財務資料（營益分析表不含金融業） | closed | Q003（指令 4 發現） | 指令 5 |
| [I010](issues/I010-t86-failed-day-cached-as-empty.md) | S2 | L1 | TWSE T86 下載失敗被快取成空的交易日（擴及 TPEx、BWIBBU、MI_QFIIS、SBL、STOCK_DAY、MOPS） | closed | I007 重爬（指令 5） | 2026-11-26 |
| [I011](issues/I011-raw-and-normalized-immv-diverge.md) | S3 | L6 | 上游中途失敗時 raw 與正規化 IMMV 拿到不同回應 | closed（§10.5 refresh 流程） | 2026-11-25 重建 | 2026-11-26 |
| [I012](issues/I012-finmind-dividend-window-drops-event.md) | S2 | L1 | FinMind 股利以除息日查詢拿到上一次股利 | closed | I011 調查 | 2026-11-26 |

## 覆蓋矩陣（pop 表 × 問題）

沒有任何問題覆蓋的表，是下一輪出題的優先目標。

| pop 表 | 策略維度 | 覆蓋的問題 | 最近斷言結果 |
| --- | --- | --- | --- |
| `company_valuation` | 估值 | Q001, Q005 | Q001 PASS（含上櫃）；Q005 PASS |
| `market_valuation_summary` | 估值、市場 | — | — |
| `monthly_revenue_twse` | 成長 | Q002 | Q002 PASS（含 PIT 斷言 C3） |
| `monthly_revenue` | 成長 | — | — |
| `mops_quarterly_financials` | 成長 | Q002, Q003 | Q002 C4、C5、Q003 C4 PASS |
| `financial_quarterly_yfinance` | 成長 | — | — |
| `financial_yearly_yfinance` | 體質 | — | — |
| `financial_analysis` | 體質 | Q003 | Q003 PASS |
| `raw_yearly_financials`（FinMind，無正規化表） | 體質 | — | — |
| `dividend` | 股利 | Q005 | Q005 PASS（I012 修正後可比對 50 組） |
| `dividend_yfinance` | 股利 | Q005 | Q005 C1 FAIL（I003，刻意保留） |
| `institutional_net_buy_sell` | 籌碼 | Q004 | Q004 PASS |
| `foreign_ownership` | 籌碼 | Q004 | Q004 PASS（含上櫃） |
| `sbl_history` | 籌碼 | Q004 | Q004 PASS |
| `ohlcv_daily` | 技術 | Q001 | Q001 PASS（C4） |
| `chain_info_list` | 產業 | — | — |
| `company_basic_info_list` | 產業 | Q002, Q003（依賴鏈） | 依賴鏈補爬成功 |
| `company_business_items` | 產業 | — | — |
| `product_revenue` | 產業 | — | — |
| `finmind_news` | 事件 | — | — |

## 品質趨勢（每次盤點一行）

| 日期 | raw 平均 usable% | 最低三個 raw（usable%） | 新開／關閉 issue |
| --- | --- | --- | --- |
| 2026-11-25 | 86.1（23 個 json 欄位） | `raw_monthly_revenue` 0.0（僅 1 列）、`raw_sbl_history` 40.8、`raw_finmind_news` 72.6 | 指令 3 新開 I001–I008 |
| 2026-11-25（指令 6 回歸） | 85.9 | `raw_monthly_revenue` 0.0、`raw_sbl_history` 46.5、`raw_finmind_news` 72.6 | 指令 4 開 I009、指令 5 開 I010；關閉 I001、I008、I009。回歸：passing 的 Q003 4／4 PASS。平均略降是因補爬新增了 I005 的上櫃估值 NULL 列 |
| 2026-11-26（重建＋重試） | 90.9 | `raw_monthly_revenue` 0.0（僅 1 列 FinMind seed）、`raw_finmind_news` 72.6（26 列為 404，無新聞）、`raw_sbl_history` 77.0（85 列為 404） | 開 I011、I012；關閉 I002、I003、I005、I006、I007、I010、I011、I012。Q001–Q004 全 PASS，Q005 只剩刻意保留的 C1 |

## 最新盤點發現（2026-11-26，`snapshots/2026-11-26.json`）

依使用者要求，照 repo README 刪除並重建 `poc`、`pop`、`hidden` 三個 schema（README §10.6），再還原 seed、跑 `retry_failed.py`：

- **usable% 變化（對 2026-11-25）**：`raw_company_valuation` 89.5 → 99.8、`raw_monthly_revenue_twse` 75.1 → 100、`raw_sbl_history` 40.8 → 77.0、`raw_financial_analysis` 88.3 → 94.8、`raw_yearly_financials` 83.0 → 100、`raw_dividend_history` 90.4 → 100、`raw_dividend` 95.1 → 100、`raw_institutional_net_buy_sell` 100 → 100（533 列，假日回溯後取樣日不同）。
- **剩下的非可用列多為正當的 404**：`raw_sbl_history` 85 列（該月無借券）、`raw_finmind_news` 26 列（2330 2019–2024 部分月份 FinMind 無新聞）、`raw_financial_analysis` 15 列。`raw_product_revenue` 2 列仍是 MOPS 失敗，`raw_mops_quarterly_financials` 1 列是 8463 上市前。
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

## 重爬紀錄（crawl_delete_rows → crawl_insert_filtered）

| 日期 | seed | filters | 刪除列數 | pinned | 重爬後狀態 | 相關 issue |
| --- | --- | --- | ---: | ---: | --- | --- |
| 2026-11-25 | `sbl_history_monthly_list` | `{stk_code: "2330", month_start_date: "2015-08-05"}` | 1 | 1 | 原本 NULL（404）→ 可用，49 筆借券紀錄 | 指令 0 實測 |
| 2026-11-25 | `mops_quarterly_financials_quarter_list` | 16 檔全部（依 http_log 還原 356 組） | 355 | 355 | 單季值；NULL 12 → 1（8463 上市前） | I001、I009 |
| 2026-11-25 | `financial_analysis_year_list` | `{year: 2026}` | 21 | 21 | 不再產生（seed 終點修正） | I008 |
| 2026-11-25 | `dividend_event_yfinance_list` | 126 檔、最後一次 split 之前的事件 | 163 | 163 | 還原成實際配發金額 | I003 |
| 2026-11-25 | `institutional_monthly_list` | `row` 為空的 448 列 | 448 | 448 | **中斷**：288 列重新 insert（多數仍空）、160 列待爬；TWSE 開始封鎖 | I007、I010 |
| 2026-11-26 | 全部 seed（重建） | §10.6：`create_views()` → `create_mat_views(recreate=True)`，再依備份還原 seed key | — | — | 零漂移；TWSE 中途 307 封鎖造成的 NULL 交給 retry_failed | I002、I005、I006 |
| 2026-11-26 | 13 個 raw 的 seed | `retry_failed.py --apply`（NULL 或 `source_errors`；OHLCV 加 `--include-not-found`） | ≈1,240 | 同左 | OHLCV 空月 454 → 0、月營收 111 → 0、FinMind 年報／股利歷史全數可用；被 rollback 的分歧列見 I011 | I007、I010、I011 |
| 2026-11-26 | `dividend_event_list` | `refresh_immv` raw_dividend → dividend | 51 | — | 可用 33 → 51，reference_date＝as_of 51／51 | I011、I012 |
| 2026-11-26 | `institutional_monthly_list` | `refresh_immv` raw → 正規化 | 533 | — | 失敗 11 → 0 | I011 |
| 2026-11-26 | 8 張正規化 IMMV 的 seed | `pin_seed_responses` → `update_mat_views` → `unpin_responses` | — | 3,586 | 列數不變、NULL 主鍵 0、上游呼叫 0 | I004 |
