---
id: Q025
title: 上櫃 5483、3105、3264、6488 月營收年增率是否連 3 個月 > 20%？上櫃的季營收和三個月月營收合計對得上嗎？
strategy: growth             # §5.1 成長（上櫃邊界）
status: passing
stocks: ["5483", "3105", "3264", "6488"]
window: {since: 2025-10-01, until: 2026-11-27}
pop_tables: [monthly_revenue_twse, mops_quarterly_financials]
crawl:                       # 出題時（2026-11-27）6488 已由 [Q002](Q002-growth-revenue-momentum.md) 補過；其他三檔都沒有月營收與季報
  - seed: financial_month_twse_list
    filters: {stk_code: ["5483", "3105", "3264", "6488"]}
    order_by: ["as_of_date"]
    since: 2025-11-10
    budget: 39               # TPEx t187ap05_O＋MOPS otc t21sc03；3 檔 × 13 個月（2025/10 起，讓 2025Q4 有三個月）
  - seed: mops_quarterly_financials_quarter_list
    filters: {stk_code: ["5483", "3105", "3264", "6488"]}
    order_by: ["quarter_end"]
    since: 2025-12-31
    budget: 12               # MOPS t163sb06；3 檔 × 2025Q4–2026Q3（兩個 seed 合計 51，§8.3 MOPS 上限 60）
issues: []
last_run: 2026-11-27
---

## 決策情境
最近 3 個月的月營收年增率都 > 20% → 「成長動能強」，列入觀察；有任一個月 < 0 → 不列入。
上櫃月營收走的是 TPEx OpenAPI＋MOPS `otc/t21sc03` 彙總頁（[I023](../issues/I023-mops-revenue-page-truncated.md) 的截斷發生在 `sii` 頁，`otc` 頁也可能發生），C2、C3 FAIL 時結論不可信。

## 決策 SQL
```sql
WITH t AS (
  SELECT stk_code, as_of, latest_month_label, latest_month_yoy_pct,
         row_number() OVER (PARTITION BY stk_code ORDER BY as_of DESC) AS rn
  FROM pop.monthly_revenue_twse
  WHERE stk_code IN ('5483','3105','3264','6488') AND as_of >= DATE '2025-11-10' AND latest_month_value IS NOT NULL
)
SELECT stk_code,
       string_agg(latest_month_label || ' ' || round(latest_month_yoy_pct::numeric, 1), '、' ORDER BY as_of) FILTER (WHERE rn <= 3) AS last3,
       bool_and(latest_month_yoy_pct > 20) FILTER (WHERE rn <= 3) AS last3_over_20,
       bool_or(latest_month_yoy_pct < 0) FILTER (WHERE rn <= 3) AS any_negative
FROM t GROUP BY stk_code ORDER BY stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 每檔每月 10 日都有月營收（13 個月），2025Q4–2026Q3 每季都有季營收
```sql
WITH em AS (
  SELECT s.stk_code, (g + INTERVAL '9 days')::date AS as_of
  FROM unnest(ARRAY['5483','3105','3264','6488']) AS s(stk_code)
  CROSS JOIN generate_series(DATE '2025-11-01', CURRENT_DATE - 9, INTERVAL '1 month') AS g
), eq AS (
  SELECT s.stk_code, y, q
  FROM unnest(ARRAY['5483','3105','3264','6488']) AS s(stk_code)
  CROSS JOIN (VALUES (2025, 4), (2026, 1), (2026, 2), (2026, 3)) AS v(y, q)
)
SELECT stk_code, as_of::text AS missing, 'monthly' AS kind FROM em
WHERE NOT EXISTS (SELECT 1 FROM pop.monthly_revenue_twse m WHERE m.stk_code = em.stk_code AND m.as_of = em.as_of AND m.latest_month_value IS NOT NULL)
UNION ALL
SELECT stk_code, y || 'Q' || q, 'quarterly' FROM eq
WHERE NOT EXISTS (SELECT 1 FROM pop.mops_quarterly_financials f WHERE f.stk_code = eq.stk_code AND f.fiscal_year = eq.y AND f.quarter = eq.q AND f.revenue_millions IS NOT NULL)
```

### C2 cross-source — 季營收（MOPS t163sb06，百萬元）= 該季三個月月營收合計（TPEx／MOPS t21sc03）（誤差 2%）
同 [Q002](Q002-growth-revenue-momentum.md) C5，換成上櫃。兩個不同的 MOPS 頁面與 adapter。
```sql
WITH q AS (
  SELECT DISTINCT stk_code, fiscal_year::int AS y, quarter::int AS qtr, revenue_millions
  FROM pop.mops_quarterly_financials WHERE stk_code IN ('5483','3105','3264','6488') AND revenue_millions IS NOT NULL
), m AS (
  SELECT DISTINCT stk_code, to_date(latest_month_label, 'YYYY/MM') AS month, latest_month_value
  FROM pop.monthly_revenue_twse WHERE stk_code IN ('5483','3105','3264','6488') AND latest_month_value IS NOT NULL
), j AS (
  SELECT q.stk_code, q.y, q.qtr, q.revenue_millions,
         round(sum(m.latest_month_value) / 1e6, 2) AS monthly_sum_millions, count(*) AS n_months
  FROM q JOIN m ON m.stk_code = q.stk_code AND extract(year FROM m.month) = q.y
               AND ceil(extract(month FROM m.month) / 3.0) = q.qtr
  GROUP BY 1, 2, 3, 4
)
SELECT * FROM j WHERE n_months = 3 AND abs(revenue_millions - monthly_sum_millions) > 0.02 * monthly_sum_millions
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；補爬後預期 ≥ 16：4 檔 × 2025Q4–2026Q3，6488 另有更早的季度）：
```sql
WITH q AS (
  SELECT DISTINCT stk_code, fiscal_year::int AS y, quarter::int AS qtr
  FROM pop.mops_quarterly_financials WHERE stk_code IN ('5483','3105','3264','6488') AND revenue_millions IS NOT NULL
), m AS (
  SELECT DISTINCT stk_code, to_date(latest_month_label, 'YYYY/MM') AS month
  FROM pop.monthly_revenue_twse WHERE stk_code IN ('5483','3105','3264','6488') AND latest_month_value IS NOT NULL
)
SELECT count(*) FROM (
  SELECT q.stk_code, q.y, q.qtr FROM q JOIN m ON m.stk_code = q.stk_code
    AND extract(year FROM m.month) = q.y AND ceil(extract(month FROM m.month) / 3.0) = q.qtr
  GROUP BY 1, 2, 3 HAVING count(*) = 3
) c
```

### C3 uniqueness — 同一檔同一營收月只出現在一個 as_of（[I023](../issues/I023-mops-revenue-page-truncated.md) 的失效模式，這次在 `otc` 頁）
```sql
SELECT stk_code, latest_month_label, array_agg(as_of ORDER BY as_of) AS as_ofs
FROM pop.monthly_revenue_twse WHERE stk_code IN ('5483','3105','3264','6488') AND latest_month_label IS NOT NULL
GROUP BY 1, 2 HAVING count(*) > 1
```

### C4 point-in-time — 營收月的公告期限（次月 10 日）不晚於 as_of；季報的 `available_date` 不晚於 as_of
```sql
SELECT 'monthly' AS kind, stk_code, as_of, latest_month_label AS period FROM pop.monthly_revenue_twse
WHERE stk_code IN ('5483','3105','3264','6488') AND latest_month_label IS NOT NULL
  AND (to_date(latest_month_label, 'YYYY/MM') + INTERVAL '1 month 9 days')::date > as_of
UNION ALL
SELECT 'quarterly', stk_code, as_of, fiscal_year || 'Q' || quarter FROM pop.mops_quarterly_financials
WHERE stk_code IN ('5483','3105','3264','6488') AND available_date > as_of
```

### C5 precision — 月營收是 1,000 元的整數倍（上游單位仟元）
```sql
SELECT stk_code, as_of, latest_month_value FROM pop.monthly_revenue_twse
WHERE stk_code IN ('5483','3105','3264','6488') AND latest_month_value % 1000 <> 0
```

## 已知限制
- 2% 門檻同 [Q002](Q002-growth-revenue-momentum.md) C5：季報營收是合併報表、月營收也是合併，但季報可能有期後調整；超過 2% 先查是不是更正公告。
- 決策只看年增率，沒有看季節性；上櫃小型股年增率波動大。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-27 | passing（5／5） | [run](../runs/2026-11-27_Q025.md) | 補爬 MOPS 51 列；3105、3264 成長動能強；3264 營收抽查一致 |
