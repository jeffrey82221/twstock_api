---
id: I014
title: yfinance「空結果」、上櫃零股日、FinMind 限流殘留——合法答案被當失敗，失敗則被當合法
severity: S3
layer: L1（adapter：yfinance_source、valuation_source）
status: closed
pop_tables: [raw_dividend_history_yfinance, raw_yearly_financials_yfinance, raw_company_valuation, raw_monthly_revenue]
seeds: [company_list, financial_year_yfinance_list, company_valuation_monthly_list, financial_month_list]
found_by: [2026-11-26 盤點剩餘的不可用列]
---

## 症狀（附可重現的 SQL）

```sql
SELECT 'yf_div_hist', count(*) FROM pop.raw_dividend_history_yfinance WHERE dividend_history ? 'source_errors'
UNION ALL SELECT 'yf_yearly', count(*) FROM pop.raw_yearly_financials_yfinance WHERE financials ? 'source_errors'
UNION ALL SELECT 'valuation_null', count(*) FROM pop.raw_company_valuation WHERE company_valuation IS NULL
UNION ALL SELECT 'finmind_rev', count(*) FROM pop.raw_monthly_revenue WHERE revenue ? 'source_errors'
```

修復前依序為 184、2、3、1。

## 證據與根因

1. **yfinance 空結果一律記為 source error**：`Ticker.dividends` 與 `quarterly_financials` 碰到網路錯誤或無效代號時也是回空、不拋例外，adapter 因此把所有空結果都當失敗。結果有 30 檔上市櫃公司 yfinance 有股價、就是沒有股利資料（例：1443 兩邊都沒有；1453 FinMind 有 13 次配息，但 Yahoo 沒收錄），被 `retry_failed` 永遠重打，品質報表也算成失敗。財報有 2 檔新上市公司（6913、7772）是同樣情況。
2. **上櫃零股日**：1264（德麥）2025-09-05 等 3 天只有零股成交，TPEx dailyQuotes 收盤欄是「---」。`get_company_valuation` 直接回 404 `no_price`，沒有在回溯窗口內往前找。
3. **FinMind 月營收 1 列**：當初抓的時候遇到限流（`source_errors`），但 `retry_failed.py` 沒把 `raw_monthly_revenue` 列進去，先前還被誤判成「僅 1 列 seed 的合法無資料」。

## 修復紀錄

- `app/yfinance_source.py`：
  - 新增 `_yf_has_prices_sync()`。股利或財報為空時，多查一次近 1 個月股價：
    - 有股價 → 明確的「沒有資料」，不記錯誤、可快取。
    - 沒股價 → 記 source error、不快取。
  - `get_dividend_yf` 與 `get_financial_statements_yf` 都改成這個邏輯。
- `app/service.py`：`query_financials_yfinance` 的 `found` 改為 `bool(fs_rows)`，Yahoo 沒收錄時回 `found=false`，不再回「found=true、全部欄位 null」。
- `app/valuation_source.py`：該股當天無收盤價、但同一張表其他股票有收盤價時，在 `MAX_LOOKBACK_DAYS` 內往前找該股最近一個有收盤價的交易日。舊版 BWIBBU_d 整張表沒有收盤價欄，這種情況不回溯。
- `verify_questions/retry_failed.py`：補上 `raw_monthly_revenue` 與 4 個 yfinance raw。
- 測試：`test_yfinance_dividend_source.py` 新增 3 個、`test_valuation_source.py` 新增 2 個。

## 重爬與驗證（2026-11-26）

- `raw_dividend_history_yfinance`：source error 184 → 154。剩下的 154 列全是 `company_list` 裡的興櫃或下市代號，yfinance 連股價都沒有，與 `raw_company_info` 的 187 列「查無上市櫃基本資料」是同一群代號。上市櫃代號的失敗列 = 0。
  - 4587 這列重新 insert 時逾時，已用 `reinsert` 補回。
- `raw_yearly_financials_yfinance`：2 → 0，6913、7772 改為 `found=false`、沒有 source error。
- `raw_company_valuation`：NULL 3 → 0，1264 取前一交易日 2025-09-04 的收盤價。
- `raw_monthly_revenue`：1 → 0。
- raw 平均可用率 90.9% → 95.4%，`run_checks --all`：23 PASS，1 FAIL（Q005 C1，刻意保留）。

## 後續

`company_list` 這個 seed 包含興櫃與下市代號，所以 `raw_company_info`、`raw_dividend_history_yfinance` 等對這些代號的呼叫可能是空的。使用者決定只用上市櫃公司後，另開 [I015](I015-company-list-scope-unlisted-codes.md) 處理：yfinance 那條路已改用 `company_basic_info_list`；FinMind 那條路實測有 70 家非上市櫃公司真的有股利歷史，沒有動，待使用者決定。
