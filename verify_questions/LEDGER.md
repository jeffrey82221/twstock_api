# Ledger

> 由 AI 指令維護（見 [README §6.4](README.md#64-ledgermd)）。手動修改也可以，AI 會以這裡的內容為準。

## 問題

| ID | 策略 | 標題 | 狀態 | 最後執行 | 未結 issue |
| --- | --- | --- | --- | --- | --- |
| [Q001](questions/Q001-valuation-per-percentile.md) | 估值 | 2330、2317、6488 本益比在近 3 年第幾百分位 | failing | 2026-11-25 | I004, I005 |
| [Q002](questions/Q002-growth-revenue-momentum.md) | 成長 | 2330、2454、6488 月營收 YoY 連 3 月 > 20%，季營收對得起來 | failing | 2026-11-25 | I001, I002 |
| [Q003](questions/Q003-quality-roe-debt.md) | 體質 | 2330、2882、1101 連 3 年 ROE > 15% 且負債比 < 50% | failing | 2026-11-25 | I001, I008 |
| [Q004](questions/Q004-flow-foreign-institutional.md) | 籌碼 | 2330、2317、6488 外資持股、法人買賣超、借券趨勢 | failing | 2026-11-25 | I006, I007 |
| [Q005](questions/Q005-income-dividend-consistency.md) | 股利 | 2330、2317、2882、1101 連 5 年配息與殖利率 | failing | 2026-11-25 | I003 |

## Issues

| ID | 嚴重度 | 層級 | 標題 | 狀態 | 發現於 | 修復 commit |
| --- | --- | --- | --- | --- | --- | --- |
| [I001](issues/I001-mops-quarterly-financials-ytd.md) | S1 | L1 | mops_quarterly_financials 營收與 EPS 為年初至今累計 | diagnosed | Q002, Q003 | — |
| [I002](issues/I002-point-in-time-look-ahead.md) | S1 | L2／L3 設計（待決定） | as_of 看得到尚未公布的月營收與季報 | diagnosed | Q002 | — |
| [I003](issues/I003-yfinance-dividend-adjusted.md) | S1 | L1＋L0 | dividend_yfinance 現金股利為配股還原值 | diagnosed | Q005 | — |
| [I004](issues/I004-normalized-as-of-from-response.md) | S2 | L5 | 正規化 view 的 as_of 取自回應，失敗列主鍵為 NULL | diagnosed | Q001 | — |
| [I005](issues/I005-otc-valuation-missing.md) | S2 | L1 新上游（待研究） | 上櫃股沒有估值資料 | diagnosed | Q001 | — |
| [I006](issues/I006-otc-foreign-ownership-missing.md) | S2 | L1 新上游（待研究） | 上櫃股沒有外資持股資料 | diagnosed | Q004 | — |
| [I007](issues/I007-institutional-weekend-no-fallback.md) | S2 | L1 | 法人買賣超取樣日遇假日不回溯 | diagnosed | Q004 | — |
| [I008](issues/I008-financial-analysis-current-year.md) | S4 | L3 | financial_analysis_year_list 產生當年度 | diagnosed | Q003 | — |
| [I009](issues/I009-mops-quarterly-financial-companies.md) | S2 | L1 | 金融股沒有季財務資料（營益分析表不含金融業） | diagnosed | Q003（指令 4 發現） | — |

## 覆蓋矩陣（pop 表 × 問題）

沒有任何問題覆蓋的表，是下一輪出題的優先目標。

| pop 表 | 策略維度 | 覆蓋的問題 | 最近斷言結果 |
| --- | --- | --- | --- |
| `company_valuation` | 估值 | Q001, Q005 | Q001 FAIL（6488）；Q005 PASS |
| `market_valuation_summary` | 估值、市場 | — | — |
| `monthly_revenue_twse` | 成長 | Q002 | Q002 PASS（C1、C2、C6）／FAIL（C3 前視） |
| `monthly_revenue` | 成長 | — | — |
| `mops_quarterly_financials` | 成長 | Q002, Q003 | FAIL（累計值、前視） |
| `financial_quarterly_yfinance` | 成長 | — | — |
| `financial_yearly_yfinance` | 體質 | — | — |
| `financial_analysis` | 體質 | Q003 | Q003 PASS（C1、C3）／FAIL（C2 當年度） |
| `raw_yearly_financials`（FinMind，無正規化表） | 體質 | — | — |
| `dividend` | 股利 | Q005 | Q005 PASS |
| `dividend_yfinance` | 股利 | Q005 | Q005 FAIL（還原值） |
| `institutional_net_buy_sell` | 籌碼 | Q004 | Q004 FAIL（假日） |
| `foreign_ownership` | 籌碼 | Q004 | Q004 FAIL（上櫃） |
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

## 最新盤點發現（2026-11-25，`snapshots/2026-11-25.json`）

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
