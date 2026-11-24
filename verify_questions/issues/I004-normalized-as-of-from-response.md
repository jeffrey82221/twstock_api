---
id: I004
title: 正規化 view 的 as_of 取自 API 回應，回應失敗時主鍵變成 NULL
severity: S2
layer: L5
status: diagnosed
pop_tables: [company_valuation, dividend, dividend_yfinance, finmind_news, institutional_net_buy_sell, market_valuation_summary, monthly_revenue, mops_quarterly_financials, financial_quarterly_yfinance, financial_yearly_yfinance]
seeds: []
found_by: [Q001]
---

## 症狀（附可重現的 SQL）

`company_valuation.sql` 以 `custom.parse_iso_date(company_valuation->>'date') AS as_of` 取主鍵，回應為 NULL 時 as_of 也是 NULL。
6488 的 43 列 `company_valuation` 全部 `as_of IS NULL`：以日期為鍵的查詢與斷言完全看不到這些失敗列（Q001 原本的 C2 因此誤判 PASS）。

同樣寫法（as_of 取自回應 JSON）的正規化 view 共 10 張：`company_valuation`、`dividend`、`dividend_yfinance`、`finmind_news`、
`institutional_net_buy_sell`、`market_valuation_summary`、`monthly_revenue`、`mops_quarterly_financials`、
`financial_quarterly_yfinance`、`financial_yearly_yfinance`。

```sql
SELECT 'company_valuation' AS t, count(*) FILTER (WHERE as_of IS NULL) AS null_key_rows, count(*) AS total FROM pop.company_valuation
UNION ALL SELECT 'institutional_net_buy_sell', count(*) FILTER (WHERE as_of IS NULL), count(*) FROM pop.institutional_net_buy_sell
UNION ALL SELECT 'finmind_news', count(*) FILTER (WHERE as_of IS NULL), count(*) FROM pop.finmind_news
```

## 證據
- 10 張正規化 view 以回應 JSON 的日期當 as_of；raw 表本身都有 seed 的 as_of（或 `valuation_date`）。
- 成功列中「回應日期 ≠ seed 日期」的列數：**10 張全部為 0**（逐一比對）→ 改用 seed 欄位不會改變任何既有值，只會補上失敗列的主鍵。
- 目前實際受影響：`company_valuation` 211 列（上櫃股，I005）、`mops_quarterly_financials` 12 列（2882，I009）。`finmind_news` 的 26 個失敗列因 LATERAL 攤平本來就是 0 列，不受影響；其他 7 張目前沒有失敗列（潛在問題）。

## 根因

正規化層把主鍵取自 API 回應而非 seed，違反「`_list.sql` 的欄位即為下游 view 的 primary key」（db/poc rule 7）。L5。

## 修復方案

1. 10 張 view 的 `as_of` 改為 raw 的 seed 欄位（`raw.as_of`；`market_valuation_summary` 用 `valuation_date`）。rule 18：欄位、主鍵、成功列的值都不變 → 就地修正。
2. 上線：`poc_rebuild_views` → `pop_check_definitions` → `pop_update_mat_views`（IMMV DROP + 重建 = 對既有 seed 列重打 API）。重打成本：company_valuation 1,684（TWSE BWIBBU_d 依日期磁碟快取，約 115 個日期）、mops 355（MOPS 磁碟快取）、dividend 57 + finmind_news 95 + monthly_revenue 1（FinMind，共 153，須在 300 req/hr 內）、yfinance 三張共 2,366、institutional 682、market 115。
3. **分批**：第一批只做實際受影響的 `company_valuation`、`mops_quarterly_financials`（與 I001 一起重建）；FinMind 三張待額度空檔、yfinance 三張另排時段。

## 修復紀錄

## 重爬與驗證
