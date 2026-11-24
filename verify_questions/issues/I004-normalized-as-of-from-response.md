---
id: I004
title: 正規化 view 的 as_of 取自 API 回應，回應失敗時主鍵變成 NULL
severity: S2
layer: null
status: open
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

（指令 4 填寫）

## 根因

（指令 4 填寫）

## 修復方案

（指令 4 填寫）

## 修復紀錄

## 重爬與驗證
