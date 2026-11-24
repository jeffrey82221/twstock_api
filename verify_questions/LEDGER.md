# Ledger

> 由 AI 指令維護（見 [README §6.4](README.md#64-ledgermd)）。手動修改也可以，AI 會以這裡的內容為準。

## 問題

| ID | 策略 | 標題 | 狀態 | 最後執行 | 未結 issue |
| --- | --- | --- | --- | --- | --- |

## Issues

| ID | 嚴重度 | 層級 | 標題 | 狀態 | 發現於 | 修復 commit |
| --- | --- | --- | --- | --- | --- | --- |

## 覆蓋矩陣（pop 表 × 問題）

沒有任何問題覆蓋的表，是下一輪出題的優先目標。

| pop 表 | 策略維度 | 覆蓋的問題 | 最近斷言結果 |
| --- | --- | --- | --- |
| `company_valuation` | 估值 | — | — |
| `market_valuation_summary` | 估值、市場 | — | — |
| `monthly_revenue_twse` | 成長 | — | — |
| `monthly_revenue` | 成長 | — | — |
| `mops_quarterly_financials` | 成長 | — | — |
| `financial_quarterly_yfinance` | 成長 | — | — |
| `financial_yearly_yfinance` | 體質 | — | — |
| `financial_analysis` | 體質 | — | — |
| `raw_yearly_financials`（FinMind，無正規化表） | 體質 | — | — |
| `dividend` | 股利 | — | — |
| `dividend_yfinance` | 股利 | — | — |
| `institutional_net_buy_sell` | 籌碼 | — | — |
| `foreign_ownership` | 籌碼 | — | — |
| `sbl_history` | 籌碼 | — | — |
| `ohlcv_daily` | 技術 | — | — |
| `chain_info_list` | 產業 | — | — |
| `company_basic_info_list` | 產業 | — | — |
| `company_business_items` | 產業 | — | — |
| `product_revenue` | 產業 | — | — |
| `finmind_news` | 事件 | — | — |

## 品質趨勢（每次盤點一行）

| 日期 | raw 平均 usable% | 最低三個 raw（usable%） | 新開／關閉 issue |
| --- | --- | --- | --- |

## 重爬紀錄（crawl_delete_rows → crawl_insert_filtered）

| 日期 | seed | filters | 刪除列數 | pinned | 重爬後狀態 | 相關 issue |
| --- | --- | --- | ---: | ---: | --- | --- |
| 2026-11-25 | `sbl_history_monthly_list` | `{stk_code: "2330", month_start_date: "2015-08-05"}` | 1 | 1 | 原本 NULL（404）→ 可用，49 筆借券紀錄 | 指令 0 實測 |
