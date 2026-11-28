---
id: Q007
title: 2330、2454、6488、2882、7769 近四季 EPS 年增是否 > 20%？yfinance 與 MOPS 的季 EPS 對得起來嗎？
strategy: growth
status: draft
stocks: ["2330", "2454", "6488", "2882", "7769"]
window: {since: 2024-10-01, until: 2026-11-26}
pop_tables: [mops_quarterly_financials, financial_quarterly_yfinance, financial_yearly_yfinance]
crawl:
  - seed: mops_quarterly_financials_quarter_list
    filters: {stk_code: ["2330", "2454", "6488", "2882", "7769"]}
    order_by: ["quarter_end"]
    budget: 20               # MOPS；每檔最近 8 季，已有的不重爬（2330／2454／6488 已有 2024Q3 起）
  - seed: financial_year_yfinance_list
    filters: {stk_code: ["2330", "2454", "6488", "2882", "7769"]}
    order_by: ["year_start_date"]
    budget: 5                # yfinance
  - seed: financial_quarter_yfinance_list
    filters: {stk_code: ["2330", "2454", "6488", "2882", "7769"]}
    order_by: ["quater"]
    budget: 25               # yfinance；季的 seed 由年表回應的 ttm_quarters 產生
issues: [I016]
last_run: null
---

## 決策情境
近四季 EPS 合計（TTM）比前四季成長 > 20% → 列入成長股觀察名單。決策用 MOPS（官方、point-in-time）；
yfinance 只當交叉驗證。上櫃（6488）、金融（2882）、近一年新上市（7769，2025-11-27 上市）都要算得出來。

## 決策 SQL
```sql
WITH q AS (
  SELECT stk_code, data_date, eps,
         row_number() OVER (PARTITION BY stk_code ORDER BY data_date DESC) AS rn
  FROM pop.mops_quarterly_financials
  WHERE stk_code IN ('2330','2454','6488','2882','7769')
)
SELECT stk_code,
       max(data_date) FILTER (WHERE rn = 1) AS latest_quarter,
       sum(eps) FILTER (WHERE rn <= 4) AS eps_ttm,
       sum(eps) FILTER (WHERE rn BETWEEN 5 AND 8) AS eps_ttm_prev,
       round(100 * (sum(eps) FILTER (WHERE rn <= 4) / NULLIF(sum(eps) FILTER (WHERE rn BETWEEN 5 AND 8), 0) - 1), 1) AS ttm_yoy_pct,
       count(*) FILTER (WHERE rn <= 8) AS quarters_used
FROM q
GROUP BY stk_code
ORDER BY stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — MOPS 有 2024Q4–2026Q3 連續 8 季（7769 從上市後那季起；2882 金融業 Q3 期限 11/14 已過）
```sql
WITH e AS (
  SELECT s.stk_code, (date_trunc('quarter', g) + INTERVAL '3 months' - INTERVAL '1 day')::date AS quarter_end
  FROM unnest(ARRAY['2330','2454','6488','2882','7769']) AS s(stk_code)
  CROSS JOIN generate_series(DATE '2024-10-01', DATE '2026-07-01', INTERVAL '3 months') AS g
)
SELECT e.stk_code, e.quarter_end AS missing_quarter
FROM e
WHERE NOT EXISTS (SELECT 1 FROM pop.mops_quarterly_financials m
                  WHERE m.stk_code = e.stk_code AND m.data_date = e.quarter_end)
  AND NOT (e.stk_code = '7769' AND e.quarter_end < DATE '2025-12-31')
```

### C2 availability — yfinance 季表與年表的 TTM 欄位有值
```sql
SELECT 'quarterly' AS tbl, stk_code, as_of, latest_quarter_date, latest_quarter_eps, eps_ttm, revenue_ttm
FROM pop.financial_quarterly_yfinance
WHERE stk_code IN ('2330','2454','6488','2882','7769') AND (eps_ttm IS NULL OR latest_quarter_eps IS NULL)
UNION ALL
SELECT 'yearly', stk_code, as_of, latest_quarter_date, latest_quarter_eps, eps_ttm, revenue_ttm
FROM pop.financial_yearly_yfinance
WHERE stk_code IN ('2330','2454','6488','2882','7769') AND (eps_ttm IS NULL OR latest_quarter_eps IS NULL)
```

### C3 cross-source — yfinance 最新一季 EPS = MOPS 同季單季 EPS（誤差 0.05 元或 2%）
```sql
WITH y AS (
  SELECT stk_code, as_of, latest_quarter_date, latest_quarter_eps FROM pop.financial_quarterly_yfinance
  UNION ALL
  SELECT stk_code, as_of, latest_quarter_date, latest_quarter_eps FROM pop.financial_yearly_yfinance
)
SELECT y.stk_code, y.as_of, y.latest_quarter_date, y.latest_quarter_eps AS yfinance_eps, m.eps AS mops_eps
FROM y JOIN pop.mops_quarterly_financials m ON m.stk_code = y.stk_code AND m.data_date = y.latest_quarter_date
WHERE y.stk_code IN ('2330','2454','6488','2882','7769')
  AND abs(y.latest_quarter_eps - m.eps) > GREATEST(0.05, 0.02 * abs(m.eps))
```
可比對列數（必須 > 0，否則 INCONCLUSIVE）：
```sql
WITH y AS (
  SELECT stk_code, latest_quarter_date, latest_quarter_eps FROM pop.financial_quarterly_yfinance
  UNION ALL
  SELECT stk_code, latest_quarter_date, latest_quarter_eps FROM pop.financial_yearly_yfinance
)
SELECT count(*) FROM y JOIN pop.mops_quarterly_financials m ON m.stk_code = y.stk_code AND m.data_date = y.latest_quarter_date
WHERE y.stk_code IN ('2330','2454','6488','2882','7769') AND y.latest_quarter_eps IS NOT NULL
```

### C4 point-in-time — yfinance 回的最新一季，在 as_of 當天已過法定公告期限
期限：Q1 5/15、Q2 8/14（金融業 8/31，這裡一律用 8/31 從寬）、Q3 11/14、Q4 隔年 3/31（`app/pit.py`）。
```sql
WITH y AS (
  SELECT 'quarterly' AS tbl, stk_code, as_of, latest_quarter_date FROM pop.financial_quarterly_yfinance
  UNION ALL
  SELECT 'yearly', stk_code, as_of, latest_quarter_date FROM pop.financial_yearly_yfinance
)
SELECT tbl, stk_code, as_of, latest_quarter_date,
       CASE extract(month FROM latest_quarter_date)::int
         WHEN 3 THEN latest_quarter_date + 45 WHEN 6 THEN latest_quarter_date + 62
         WHEN 9 THEN latest_quarter_date + 45
         ELSE make_date(extract(year FROM latest_quarter_date)::int + 1, 3, 31) END AS deadline
FROM y
WHERE stk_code IN ('2330','2454','6488','2882','7769') AND latest_quarter_date IS NOT NULL
  AND CASE extract(month FROM latest_quarter_date)::int
        WHEN 3 THEN latest_quarter_date + 45 WHEN 6 THEN latest_quarter_date + 62
        WHEN 9 THEN latest_quarter_date + 45
        ELSE make_date(extract(year FROM latest_quarter_date)::int + 1, 3, 31) END > as_of
```

### C5 validity — MOPS 單季 EPS 在合理範圍（-50 ～ 200 元）
```sql
SELECT stk_code, data_date, eps
FROM pop.mops_quarterly_financials
WHERE stk_code IN ('2330','2454','6488','2882','7769') AND data_date >= DATE '2024-10-01'
  AND (eps IS NULL OR eps NOT BETWEEN -50 AND 200)
```

## 已知限制
- 決策 SQL 的 `rn` 假設季度連續；C1 FAIL 時決策結果不可信。
- 7769 在 2025-11-27 上市，上市前的季度 MOPS 沒有，C1 從 2025Q4 起要求。
- yfinance 的季資料只保留最近約 5 季（出題時實測 2330：2025-09-30 ～ 2026-09-30），as_of 早於這個窗口的列只會得到空結果，見 [I016](../issues/I016-yfinance-financials-ttm-always-null.md)。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
