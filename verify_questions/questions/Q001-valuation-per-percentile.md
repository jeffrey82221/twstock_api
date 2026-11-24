---
id: Q001
title: 2330、2317、6488 目前本益比在自身近 3 年的第幾百分位？
strategy: valuation
status: draft
stocks: ["2330", "2317", "6488"]
window: {since: 2023-11-01, until: 2026-11-25}
pop_tables: [company_valuation, ohlcv_daily]
crawl:
  - seed: company_valuation_monthly_list
    filters: {stk_code: ["2330", "2317", "6488"]}
    order_by: ["valuation_date"]
    budget: 72               # TWSE；3 年 × 12 月 × 3 檔扣掉已爬（2330 已滿、2317 缺 24、6488 未爬）
  - seed: ohlcv_monthly_list
    filters: {stk_code: ["2330", "2317", "6488"]}
    order_by: ["month_start_date"]
    budget: 36               # TWSE／TPEx；近 12 個月
issues: []
last_run: null
---

## 決策情境
目前 PER 低於自身 3 年第 20 百分位、且殖利率高於 3 年中位數 → 列入分批買進觀察；
PER 高於第 80 百分位 → 不追價。6488（上櫃）是邊界案例：估值資料源是否涵蓋上櫃股。

## 決策 SQL
```sql
WITH v AS (
  SELECT stk_code, as_of, per, dividend_yield_pct
  FROM pop.company_valuation
  WHERE stk_code IN ('2330','2317','6488') AND as_of >= DATE '2023-11-01'
), latest AS (
  SELECT DISTINCT ON (stk_code) stk_code, as_of, per, dividend_yield_pct
  FROM v ORDER BY stk_code, as_of DESC
)
SELECT l.stk_code, l.as_of, l.per,
       round(100.0 * (SELECT count(*) FROM v WHERE v.stk_code = l.stk_code AND v.per <= l.per)
             / NULLIF((SELECT count(per) FROM v WHERE v.stk_code = l.stk_code), 0), 1) AS per_pctile,
       l.dividend_yield_pct,
       (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY dividend_yield_pct)
          FROM v WHERE v.stk_code = l.stk_code) AS yield_median,
       (SELECT count(per) FROM v WHERE v.stk_code = l.stk_code) AS n_months_with_per
FROM latest l
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 每檔每月 5 日都有一筆估值
```sql
WITH expected AS (
  SELECT s.stk_code, (date_trunc('month', g) + INTERVAL '4 days')::date AS as_of
  FROM unnest(ARRAY['2330','2317','6488']) AS s(stk_code)
  CROSS JOIN generate_series(DATE '2023-11-01', CURRENT_DATE, INTERVAL '1 month') AS g
)
SELECT e.stk_code, e.as_of AS missing_as_of
FROM expected e
LEFT JOIN pop.company_valuation v ON v.stk_code = e.stk_code AND v.as_of = e.as_of
WHERE v.stk_code IS NULL AND e.as_of <= CURRENT_DATE
```

### C2 availability — 已爬的估值列都有收盤價（raw 不是 NULL）
```sql
SELECT stk_code, as_of, data_date, close_price, reason
FROM pop.company_valuation
WHERE stk_code IN ('2330','2317','6488') AND as_of >= DATE '2023-11-01' AND close_price IS NULL
```

### C3 internal — PER／PBR 可由收盤價、EPS、BVPS 互推（誤差 5%），數值在合理範圍
```sql
SELECT stk_code, as_of, per, pbr, dividend_yield_pct, close_price, eps_ttm, bvps
FROM pop.company_valuation
WHERE stk_code IN ('2330','2317','6488') AND as_of >= DATE '2023-11-01'
  AND (per <= 0 OR per > 300 OR pbr <= 0 OR pbr > 50
       OR dividend_yield_pct < 0 OR dividend_yield_pct > 25
       OR abs(close_price / NULLIF(eps_ttm, 0) - per) > 0.05 * per
       OR abs(close_price / NULLIF(bvps, 0) - pbr) > 0.05 * pbr)
```

### C4 cross-source — 估值收盤價 = 日 K 收盤價（誤差 1%）
```sql
SELECT v.stk_code, v.data_date, v.close_price, o.close
FROM pop.company_valuation v
JOIN pop.ohlcv_daily o ON o.stk_code = v.stk_code AND o.trade_date = v.data_date
WHERE v.stk_code IN ('2330','2317','6488') AND abs(v.close_price - o.close) > 0.01 * o.close
```
可比對列數（必須 > 0，否則 INCONCLUSIVE）：
```sql
SELECT count(*) FROM pop.company_valuation v
JOIN pop.ohlcv_daily o ON o.stk_code = v.stk_code AND o.trade_date = v.data_date
WHERE v.stk_code IN ('2330','2317','6488')
```

### C5 timeliness — 實際資料日與取樣日差距 ≤ 12 天
```sql
SELECT stk_code, as_of, data_date, as_of - data_date AS lag_days
FROM pop.company_valuation
WHERE stk_code IN ('2330','2317','6488') AND data_date < as_of - 12
```

## 已知限制
- 虧損期間 PER 為 NULL，這是定義上的空值，不是缺值；百分位只算非 NULL 月份。
- C5 容忍 12 天：2022-02-05 取樣日的實際資料日是 2022-01-26（春節休市 10 天）。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
