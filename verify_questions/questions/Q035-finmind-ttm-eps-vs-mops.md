---
id: Q035
title: 2330、2317、2454 年初時點的近四季 EPS 是否比一年前高？FinMind 年度財報的 TTM EPS 和 MOPS 四季加總一樣嗎？
strategy: quality            # §5.1 體質
status: draft
stocks: ["2330", "2317", "2454"]
window: {since: 2025-01-01, until: 2026-01-01}
pop_tables: [raw_yearly_financials, mops_quarterly_financials]
crawl:                       # 出題時（2026-11-27）raw_yearly_financials 全表只有 4 列；這三檔的 MOPS 季報由 Q012 補過
  - seed: financial_year_list
    filters: {stk_code: ["2330", "2317", "2454"]}
    order_by: ["year_start_date"]
    since: 2025-01-01
    budget: 6                # FinMind；3 檔 × 2025-01-01、2026-01-01
issues: []
last_run: null
---

## 決策情境
年初（1/1）時點的近四季 EPS 比一年前的同一時點高 → 獲利趨勢向上；低 → 向下。`raw_yearly_financials`（FinMind）是唯一沒有正規化表、也沒有斷言的 raw，這題順便給它第一組斷言。C2 FAIL 時結論不可信。

## 決策 SQL
```sql
SELECT stk_code,
       max((financials->'eps'->>'ttm')::numeric) FILTER (WHERE as_of = DATE '2025-01-01') AS ttm_eps_2025_01,
       max((financials->'eps'->>'ttm')::numeric) FILTER (WHERE as_of = DATE '2026-01-01') AS ttm_eps_2026_01,
       max((financials->'eps'->>'ttm')::numeric) FILTER (WHERE as_of = DATE '2026-01-01')
         > max((financials->'eps'->>'ttm')::numeric) FILTER (WHERE as_of = DATE '2025-01-01') AS improving
FROM pop.raw_yearly_financials
WHERE stk_code IN ('2330','2317','2454') AND as_of IN (DATE '2025-01-01', DATE '2026-01-01')
GROUP BY stk_code ORDER BY stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 三檔在 2025-01-01、2026-01-01 都有 raw 列，而且 TTM EPS 有值
```sql
SELECT s.stk_code, d::date AS as_of
FROM unnest(ARRAY['2330','2317','2454']) AS s(stk_code) CROSS JOIN (VALUES (DATE '2025-01-01'), (DATE '2026-01-01')) AS v(d)
WHERE NOT EXISTS (SELECT 1 FROM pop.raw_yearly_financials r WHERE r.stk_code = s.stk_code AND r.as_of = v.d
                  AND (r.financials->'eps'->>'ttm') IS NOT NULL)
```

### C2 cross-source — FinMind 的 TTM EPS = MOPS 同四季單季 EPS 加總（誤差 0.03 元：四季各自四捨五入）
FinMind（TaiwanStockFinancialStatements）與 MOPS（t163sb06）是不同上游；用 FinMind 自己列的 `ttm_quarters` 對齊季度。
```sql
WITH r AS (
  SELECT stk_code, as_of, (financials->'eps'->>'ttm')::numeric AS finmind_ttm,
         ARRAY(SELECT jsonb_array_elements_text(financials->'eps'->'ttm_quarters'))::date[] AS quarters
  FROM pop.raw_yearly_financials WHERE stk_code IN ('2330','2317','2454') AND financials->'eps'->>'ttm' IS NOT NULL
), m AS (SELECT DISTINCT stk_code, fiscal_year, quarter, eps FROM pop.mops_quarterly_financials WHERE stk_code IN ('2330','2317','2454') AND eps IS NOT NULL)
SELECT r.stk_code, r.as_of, r.finmind_ttm, sum(m.eps) AS mops_4q, count(m.eps) AS n_q
FROM r JOIN m ON m.stk_code = r.stk_code
 AND (date_trunc('quarter', make_date(m.fiscal_year::int, m.quarter::int * 3, 1)) + INTERVAL '3 months - 1 day')::date = ANY (r.quarters)
GROUP BY 1, 2, 3
HAVING count(m.eps) = 4 AND abs(r.finmind_ttm - sum(m.eps)) > 0.03
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；C2 不限 as_of，出題時 2330 多年的列已有 12 列可比對，補爬後再加 2317、2454）：
```sql
WITH r AS (
  SELECT stk_code, as_of, ARRAY(SELECT jsonb_array_elements_text(financials->'eps'->'ttm_quarters'))::date[] AS quarters
  FROM pop.raw_yearly_financials WHERE stk_code IN ('2330','2317','2454') AND financials->'eps'->>'ttm' IS NOT NULL
), m AS (SELECT DISTINCT stk_code, fiscal_year, quarter, eps FROM pop.mops_quarterly_financials WHERE stk_code IN ('2330','2317','2454') AND eps IS NOT NULL)
SELECT count(*) FROM (
  SELECT r.stk_code, r.as_of FROM r JOIN m ON m.stk_code = r.stk_code
   AND (date_trunc('quarter', make_date(m.fiscal_year::int, m.quarter::int * 3, 1)) + INTERVAL '3 months - 1 day')::date = ANY (r.quarters)
  GROUP BY 1, 2 HAVING count(*) = 4) c
```

### C3 point-in-time — FinMind 回的最新一季，在 as_of 當天已過法定公告期限（Q1 5/15、Q2 8/14、Q3 11/14、Q4 隔年 3/31）
```sql
SELECT stk_code, as_of, financials->'eps'->>'latest_quarter_date' AS latest_q
FROM pop.raw_yearly_financials
WHERE stk_code IN ('2330','2317','2454') AND financials->'eps'->>'latest_quarter_date' IS NOT NULL
  AND (CASE extract(month FROM (financials->'eps'->>'latest_quarter_date')::date)
         WHEN 3 THEN make_date(extract(year FROM (financials->'eps'->>'latest_quarter_date')::date)::int, 5, 15)
         WHEN 6 THEN make_date(extract(year FROM (financials->'eps'->>'latest_quarter_date')::date)::int, 8, 14)
         WHEN 9 THEN make_date(extract(year FROM (financials->'eps'->>'latest_quarter_date')::date)::int, 11, 14)
         ELSE make_date(extract(year FROM (financials->'eps'->>'latest_quarter_date')::date)::int + 1, 3, 31) END) > as_of
```

### C4 availability — raw 列都是 found=true，沒有 source_errors
```sql
SELECT stk_code, as_of, financials->>'found' AS found, financials ? 'source_errors' AS has_errors
FROM pop.raw_yearly_financials
WHERE stk_code IN ('2330','2317','2454') AND (financials IS NULL OR financials->>'found' <> 'true' OR financials ? 'source_errors')
```

### C5 temporal — TTM 用到的 4 季連續（第 1 季與第 4 季相隔 273–276 天），窗口內的 as_of
全表 76 列有 2 列不連續（2001-01-01，FinMind 缺 2000Q1，I027）；本題只看窗口（2025、2026 年初）。
```sql
WITH r AS (
  SELECT stk_code, as_of, ARRAY(SELECT jsonb_array_elements_text(financials->'eps'->'ttm_quarters'))::date[] AS q
  FROM pop.raw_yearly_financials
  WHERE stk_code IN ('2330','2317','2454') AND as_of IN (DATE '2025-01-01', DATE '2026-01-01')
    AND jsonb_array_length(coalesce(financials->'eps'->'ttm_quarters', '[]'::jsonb)) = 4
)
SELECT stk_code, as_of, q, q[1] - q[4] AS span_days FROM r WHERE q[1] - q[4] NOT BETWEEN 273 AND 276
```

## 已知限制
- `raw_yearly_financials` 沒有正規化表（README §8.2），斷言直接讀 raw JSON；JSON 結構改版時這題會先壞。
- C3 用一般公司的期限；金融業 Q2 期限是 8/31，本題三檔都不是金融業。
- 2330 的 TTM 用 MOPS 基本 EPS 加總，FinMind 也是財報上的基本 EPS（2330 2026-01-01：61.2 = 14.45＋13.95＋15.36＋17.44）。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
