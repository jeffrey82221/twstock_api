---
id: Q010
title: 金控 2882、2891、2886 目前本益比是否低於自身近 3 年中位數？
strategy: valuation
status: draft
stocks: ["2882", "2891", "2886"]
window: {since: 2023-11-05, until: 2026-11-27}
pop_tables: [company_valuation, mops_quarterly_financials]
crawl:                       # 出題時：2882 估值只有 2026-06 起 6 個月、季報 12 季；2891、2886 都還沒有
  - seed: company_valuation_monthly_list
    filters: {stk_code: ["2882", "2891", "2886"]}
    order_by: ["valuation_date"]
    budget: 102              # 3 年 × 12 月 × 3 檔 − 2882 已有 6 個月（TWSE 上限 120）
  - seed: mops_quarterly_financials_quarter_list
    filters: {stk_code: ["2891", "2886"]}
    order_by: ["quarter_end"]
    budget: 32               # 3 年 × 4 季 × 2 檔 + 窗口前 4 季（MOPS 上限 60）
issues: [I019, I020]
last_run: null
---

## 決策情境
目前 PER 低於自身近 3 年中位數的 80%，且近四季 EPS（MOPS）為正 → 列入金融股分批買進觀察；
PER 高於中位數的 120% → 不追價。

## 決策 SQL
```sql
WITH v AS (
  SELECT stk_code, as_of, per FROM pop.company_valuation
  WHERE stk_code IN ('2882','2891','2886') AND as_of >= DATE '2023-11-05' AND per IS NOT NULL
), latest AS (
  SELECT DISTINCT ON (stk_code) stk_code, as_of, per FROM v ORDER BY stk_code, as_of DESC
)
SELECT l.stk_code, l.as_of, l.per,
       (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY per) FROM v WHERE v.stk_code = l.stk_code) AS per_median_3y,
       (SELECT count(*) FROM v WHERE v.stk_code = l.stk_code) AS n_months,
       (SELECT sum(eps) FROM (SELECT DISTINCT fiscal_year, quarter, eps FROM pop.mops_quarterly_financials m
                              WHERE m.stk_code = l.stk_code AND m.available_date <= l.as_of AND m.eps IS NOT NULL
                              ORDER BY fiscal_year DESC, quarter DESC LIMIT 4) q) AS mops_eps_4q
FROM latest l ORDER BY 1
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 2023-11 起每月 5 日都有估值
```sql
WITH e AS (
  SELECT s.stk_code, g::date AS as_of
  FROM unnest(ARRAY['2882','2891','2886']) AS s(stk_code)
  CROSS JOIN generate_series(DATE '2023-11-05', CURRENT_DATE, INTERVAL '1 month') AS g
)
SELECT e.stk_code, e.as_of AS missing_as_of
FROM e LEFT JOIN pop.company_valuation v ON v.stk_code = e.stk_code AND v.as_of = e.as_of
WHERE v.stk_code IS NULL
```

### C2 cross-source — 估值隱含 EPS（收盤價 ÷ PER，TWSE BWIBBU）= MOPS 近四季單季 EPS 合計（誤差 2% 或 0.05 元，排除 3 月取樣）
同 README §6.1 C4。金控目前違規，見 [I020](../issues/I020-2882-twse-per-eps-vs-mops.md)。
```sql
WITH q AS (
  SELECT DISTINCT stk_code, fiscal_year, quarter, eps, available_date
  FROM pop.mops_quarterly_financials WHERE stk_code IN ('2882','2891','2886') AND eps IS NOT NULL
), m AS (
  SELECT v.stk_code, v.as_of, v.eps_ttm, s.n_q, s.span, s.mops_4q
  FROM pop.company_valuation v
  CROSS JOIN LATERAL (
    SELECT count(*) AS n_q, sum(eps) AS mops_4q,
           max(fiscal_year * 4 + quarter) - min(fiscal_year * 4 + quarter) AS span
    FROM (SELECT fiscal_year, quarter, eps FROM q
          WHERE q.stk_code = v.stk_code AND q.available_date <= v.as_of
          ORDER BY fiscal_year DESC, quarter DESC LIMIT 4) last4) s
  WHERE v.stk_code IN ('2882','2891','2886') AND v.as_of >= DATE '2023-11-05'
    AND v.eps_ttm IS NOT NULL AND extract(month FROM v.as_of) <> 3
)
SELECT stk_code, as_of, round(eps_ttm, 3) AS twse_implied_eps, mops_4q
FROM m WHERE n_q = 4 AND span = 3 AND abs(eps_ttm - mops_4q) > greatest(0.05, 0.02 * abs(mops_4q))
```
可比對列數（必須 > 0，否則 INCONCLUSIVE）：
```sql
WITH q AS (
  SELECT DISTINCT stk_code, fiscal_year, quarter, eps, available_date
  FROM pop.mops_quarterly_financials WHERE stk_code IN ('2882','2891','2886') AND eps IS NOT NULL
), m AS (
  SELECT v.stk_code, v.as_of, s.n_q, s.span
  FROM pop.company_valuation v
  CROSS JOIN LATERAL (
    SELECT count(*) AS n_q, max(fiscal_year * 4 + quarter) - min(fiscal_year * 4 + quarter) AS span
    FROM (SELECT fiscal_year, quarter FROM q
          WHERE q.stk_code = v.stk_code AND q.available_date <= v.as_of
          ORDER BY fiscal_year DESC, quarter DESC LIMIT 4) last4) s
  WHERE v.stk_code IN ('2882','2891','2886') AND v.as_of >= DATE '2023-11-05'
    AND v.eps_ttm IS NOT NULL AND extract(month FROM v.as_of) <> 3
)
SELECT count(*) FROM m WHERE n_q = 4 AND span = 3
```

### C3 uniqueness — 每檔每季只有一列季報（[I019](../issues/I019-financial-q3-frozen-as-previous-quarter.md)）
```sql
SELECT stk_code, fiscal_year, quarter, array_agg(as_of ORDER BY as_of) AS as_ofs
FROM pop.mops_quarterly_financials
WHERE stk_code IN ('2882','2891','2886') AND fiscal_year IS NOT NULL
GROUP BY 1, 2, 3 HAVING count(*) > 1
```

### C4 point-in-time — 季報的公告日不晚於 as_of，且晚於季末
```sql
SELECT stk_code, as_of, data_date, available_date
FROM pop.mops_quarterly_financials
WHERE stk_code IN ('2882','2891','2886') AND fiscal_year IS NOT NULL
  AND (available_date > as_of OR available_date <= data_date)
```

### C5 validity — 金控 PER 介於 0 與 60、PBR 介於 0 與 5
```sql
SELECT stk_code, as_of, per, pbr FROM pop.company_valuation
WHERE stk_code IN ('2882','2891','2886') AND as_of >= DATE '2023-11-05'
  AND (per <= 0 OR per > 60 OR pbr <= 0 OR pbr > 5)
```

## 已知限制
- 虧損期間 PER 為 NULL，中位數只算非 NULL 月份。
- 金控的 TWSE 隱含 EPS 與 MOPS EPS 有 6–7% 的系統性落差（[I020](../issues/I020-2882-twse-per-eps-vs-mops.md)，待確認是否為 TWSE 的股數口徑）。[I020](../issues/I020-2882-twse-per-eps-vs-mops.md) 確認前，C2 對金控的 FAIL 不代表決策用的 PER 錯，決策只用 TWSE 公布的 PER。
- 2882 2026Q3 在 2026-11-14 取樣時被凍結成 Q2（[I019](../issues/I019-financial-q3-frozen-as-previous-quarter.md)），C3 會 FAIL；決策 SQL 用 `DISTINCT` 取季度，不受重複影響，但近四季 EPS 會少了 Q3。
- 金融業沒有毛利率等科目（[I009](../issues/I009-mops-quarterly-financial-companies.md)），本題不用。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
