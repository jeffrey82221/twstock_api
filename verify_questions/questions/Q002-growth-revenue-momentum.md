---
id: Q002
title: 2330、2454、6488 月營收 YoY 是否連續 3 個月 > 20%，且季營收與月營收對得起來？
strategy: growth
status: failing
stocks: ["2330", "2454", "6488"]
window: {since: 2025-11-01, until: 2026-11-25}
pop_tables: [monthly_revenue_twse, mops_quarterly_financials]
crawl:
  - seed: company_list                       # 2454 尚未進母體（§8.1 依賴鏈）
    filters: {stk_code: "2454"}
    budget: 1
  - seed: company_basic_info_list
    filters: {stk_code: "2454"}
    budget: 1
  - seed: financial_month_twse_list
    filters: {stk_code: ["2330", "2454", "6488"]}
    order_by: ["month_start_date"]
    budget: 39               # MOPS；13 個月 × 3 檔
  - seed: mops_quarterly_financials_quarter_list
    filters: {stk_code: ["2330", "2454", "6488"]}
    order_by: ["quarter_end"]
    budget: 18               # MOPS；6 季 × 3 檔
issues: [I001, I002]
last_run: 2026-11-25
---

## 決策情境
最近 3 個月月營收 YoY 皆 > 20% → 成長動能確立，列入成長股觀察；季營收必須能由月營收加總驗證，
否則不使用季度數字做判斷。另外檢查 point-in-time：回測時「當時看得到的資料」不能包含尚未公布的月份／季度。

## 決策 SQL
```sql
WITH m AS (
  SELECT stk_code, to_date(latest_month_label, 'YYYY/MM') AS month, latest_month_value, latest_month_yoy_pct
  FROM pop.monthly_revenue_twse
  WHERE stk_code IN ('2330','2454','6488') AND as_of >= DATE '2025-12-01'
), r AS (
  SELECT m.*, row_number() OVER (PARTITION BY stk_code ORDER BY month DESC) AS rn FROM m
)
SELECT stk_code,
       max(month) FILTER (WHERE rn = 1) AS latest_month,
       round(avg(latest_month_yoy_pct) FILTER (WHERE rn <= 3), 1) AS avg_yoy_3m,
       bool_and(latest_month_yoy_pct > 20) FILTER (WHERE rn <= 3) AS yoy_gt20_3m,
       count(*) AS n_months
FROM r GROUP BY stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 每檔每月 1 日都有一筆有值的月營收
```sql
WITH e AS (
  SELECT s.stk_code, g::date AS as_of
  FROM unnest(ARRAY['2330','2454','6488']) AS s(stk_code)
  CROSS JOIN generate_series(DATE '2025-12-01', DATE '2026-11-01', INTERVAL '1 month') AS g
)
SELECT e.stk_code, e.as_of, m.latest_month_value
FROM e LEFT JOIN pop.monthly_revenue_twse m ON m.stk_code = e.stk_code AND m.as_of = e.as_of
WHERE m.latest_month_value IS NULL
```

### C2 availability — 這幾檔所有已爬的月營收 raw 列都可用（含歷史）
```sql
SELECT stk_code, as_of, revenue->>'source' AS source, revenue->>'reason' AS reason
FROM pop.raw_monthly_revenue_twse
WHERE stk_code IN ('2330','2454','6488')
  AND (revenue IS NULL OR revenue->>'found' = 'false')
```

### C3 point-in-time — 月營收在法定公布期限（次月 10 日）之後才可出現在 as_of
```sql
SELECT stk_code, as_of, latest_month_label,
       (to_date(latest_month_label, 'YYYY/MM') + INTERVAL '1 month' + INTERVAL '9 days')::date AS publish_deadline
FROM pop.monthly_revenue_twse
WHERE stk_code IN ('2330','2454','6488') AND as_of >= DATE '2025-12-01'
  AND (to_date(latest_month_label, 'YYYY/MM') + INTERVAL '1 month' + INTERVAL '9 days')::date > as_of
```

### C4 point-in-time — 季報在法定期限（Q1–Q3 季末後 45 天、Q4 後 90 天）之後才可出現在 as_of
```sql
SELECT stk_code, as_of, data_date, fiscal_year, quarter
FROM pop.mops_quarterly_financials
WHERE stk_code IN ('2330','2454','6488')
  AND data_date + CASE WHEN quarter = 4 THEN 90 ELSE 45 END > as_of
```

### C5 cross-source — 季營收（MOPS，百萬元）= 該季三個月月營收合計（誤差 2%）
```sql
WITH q AS (
  SELECT stk_code, fiscal_year::int AS y, quarter::int AS qtr, revenue_millions
  FROM pop.mops_quarterly_financials WHERE stk_code IN ('2330','2454','6488')
), m AS (
  SELECT DISTINCT stk_code, to_date(latest_month_label, 'YYYY/MM') AS month, latest_month_value
  FROM pop.monthly_revenue_twse WHERE stk_code IN ('2330','2454','6488')
), j AS (
  SELECT q.stk_code, q.y, q.qtr, q.revenue_millions,
         round(sum(m.latest_month_value) / 1e6, 2) AS monthly_sum_millions, count(*) AS n_months
  FROM q JOIN m ON m.stk_code = q.stk_code AND extract(year FROM m.month) = q.y
               AND ceil(extract(month FROM m.month) / 3.0) = q.qtr
  GROUP BY 1, 2, 3, 4
)
SELECT * FROM j
WHERE n_months = 3 AND abs(revenue_millions - monthly_sum_millions) > 0.02 * monthly_sum_millions
```
可比對列數（必須 > 0，否則 INCONCLUSIVE）：
```sql
WITH q AS (
  SELECT stk_code, fiscal_year::int AS y, quarter::int AS qtr
  FROM pop.mops_quarterly_financials WHERE stk_code IN ('2330','2454','6488')
), m AS (
  SELECT DISTINCT stk_code, to_date(latest_month_label, 'YYYY/MM') AS month
  FROM pop.monthly_revenue_twse WHERE stk_code IN ('2330','2454','6488')
)
SELECT count(*) FROM (
  SELECT q.stk_code, q.y, q.qtr FROM q JOIN m ON m.stk_code = q.stk_code
    AND extract(year FROM m.month) = q.y AND ceil(extract(month FROM m.month) / 3.0) = q.qtr
  GROUP BY 1, 2, 3 HAVING count(*) = 3
) c
```

### C6 internal — ttm_value = 最近 12 個月月營收加總（誤差 0.5%）
```sql
WITH m AS (
  SELECT DISTINCT stk_code, to_date(latest_month_label, 'YYYY/MM') AS month, latest_month_value
  FROM pop.monthly_revenue_twse WHERE stk_code IN ('2330','2454','6488')
), t AS (
  SELECT r.stk_code, r.as_of, to_date(r.latest_month_label, 'YYYY/MM') AS month, r.ttm_value
  FROM pop.monthly_revenue_twse r WHERE r.stk_code IN ('2330','2454','6488') AND r.as_of >= DATE '2025-12-01'
), s AS (
  SELECT t.stk_code, t.as_of, t.ttm_value, sum(m.latest_month_value) AS sum12, count(*) AS n
  FROM t JOIN m ON m.stk_code = t.stk_code AND m.month > t.month - INTERVAL '12 months' AND m.month <= t.month
  GROUP BY 1, 2, 3
)
SELECT * FROM s WHERE n = 12 AND abs(ttm_value - sum12) > 0.005 * sum12
```

## 已知限制
- C3 用的是法定最晚公布日；公司可能提早公布，所以違規代表「無法保證當時看得到」，不一定是「當時一定看不到」。
- `monthly_revenue_twse` 每列只有「最新月」，歷史月份要靠多列 as_of 拼出來。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-25 | FAIL（C3、C4、C5） | [run](../runs/2026-11-25_Q002.md) | 月營收可信；季報累計值、前視 |
