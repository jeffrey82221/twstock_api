---
id: Q004
title: 2330、2317、6488 近 12 個月外資持股比是否上升、法人是否淨買超、借券是否增加？
strategy: flow
status: failing
stocks: ["2330", "2317", "6488"]
window: {since: 2025-12-05, until: 2026-11-25}
pop_tables: [institutional_net_buy_sell, foreign_ownership, sbl_history]
crawl:
  - seed: institutional_monthly_list
    filters: {stk_code: ["2330", "2317", "6488"]}
    order_by: ["month_start_date"]
    budget: 36               # TWSE T86／TPEx
  - seed: foreign_ownership_monthly_list
    filters: {stk_code: ["2330", "2317", "6488"]}
    order_by: ["month_start_date"]
    budget: 36               # TWSE MI_QFIIS
  - seed: sbl_history_monthly_list
    filters: {stk_code: ["2330", "2317", "6488"]}
    order_by: ["month_start_date"]
    budget: 36               # TWSE t13sa870
issues: [I006, I007]
last_run: 2026-11-25
---

## 決策情境
外資持股比 12 個月上升 > 1 個百分點、且取樣日外資淨買超為正的月份過半 → 籌碼面偏多；
同期借券賣出量明顯上升 → 有放空壓力，降低部位。6488（上櫃）是邊界案例：MI_QFIIS 與 SBL 是否涵蓋上櫃股。

## 決策 SQL
```sql
WITH fo AS (
  SELECT stk_code, as_of, foreign_investment_ratio_pct,
         first_value(foreign_investment_ratio_pct) OVER w AS first_ratio,
         last_value(foreign_investment_ratio_pct) OVER w AS last_ratio
  FROM pop.foreign_ownership
  WHERE stk_code IN ('2330','2317','6488') AND as_of >= DATE '2025-12-05'
  WINDOW w AS (PARTITION BY stk_code ORDER BY as_of ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
), inst AS (
  SELECT stk_code, count(*) FILTER (WHERE foreign_investors_net_buy_sell > 0) AS buy_days,
         count(foreign_investors_net_buy_sell) AS sampled_days
  FROM pop.institutional_net_buy_sell
  WHERE stk_code IN ('2330','2317','6488') AND as_of >= DATE '2025-12-05' GROUP BY 1
), sbl AS (
  SELECT stk_code,
         sum(quantity_lots) FILTER (WHERE transaction_date >= DATE '2026-06-01') AS lots_recent_6m,
         sum(quantity_lots) FILTER (WHERE transaction_date < DATE '2026-06-01') AS lots_prior
  FROM pop.sbl_history
  WHERE stk_code IN ('2330','2317','6488') AND as_of >= DATE '2025-12-05' GROUP BY 1
)
SELECT s.stk_code, max(fo.first_ratio) AS ratio_start, max(fo.last_ratio) AS ratio_end,
       max(inst.buy_days) AS foreign_buy_days, max(inst.sampled_days) AS sampled_days,
       max(sbl.lots_recent_6m) AS sbl_lots_recent_6m, max(sbl.lots_prior) AS sbl_lots_prior
FROM unnest(ARRAY['2330','2317','6488']) AS s(stk_code)
LEFT JOIN fo ON fo.stk_code = s.stk_code
LEFT JOIN inst ON inst.stk_code = s.stk_code
LEFT JOIN sbl ON sbl.stk_code = s.stk_code
GROUP BY s.stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 每個取樣日都取得實際交易日的法人買賣超（取樣日遇假日要回溯）
```sql
WITH e AS (
  SELECT s.stk_code, (date_trunc('month', g) + INTERVAL '4 days')::date AS as_of
  FROM unnest(ARRAY['2330','2317','6488']) AS s(stk_code)
  CROSS JOIN generate_series(DATE '2025-12-01', CURRENT_DATE, INTERVAL '1 month') AS g
)
SELECT e.stk_code, e.as_of, extract(isodow FROM e.as_of) AS iso_dow, i.trade_date
FROM e LEFT JOIN pop.institutional_net_buy_sell i ON i.stk_code = e.stk_code AND i.as_of = e.as_of
WHERE i.trade_date IS NULL
```

### C2 internal — 三大法人合計 = 外資 + 外資自營 + 投信 + 自營商
```sql
SELECT stk_code, as_of, total_institutional_net_buy_sell,
       foreign_investors_net_buy_sell + foreign_dealers_net_buy_sell
         + investment_trust_net_buy_sell + dealers_net_buy_sell AS parts
FROM pop.institutional_net_buy_sell
WHERE stk_code IN ('2330','2317','6488')
  AND total_institutional_net_buy_sell IS DISTINCT FROM
      foreign_investors_net_buy_sell + foreign_dealers_net_buy_sell
        + investment_trust_net_buy_sell + dealers_net_buy_sell
  AND trade_date IS NOT NULL
```

### C3 completeness — 每個取樣月都有外資持股比
```sql
WITH e AS (
  SELECT s.stk_code, (date_trunc('month', g) + INTERVAL '4 days')::date AS as_of
  FROM unnest(ARRAY['2330','2317','6488']) AS s(stk_code)
  CROSS JOIN generate_series(DATE '2025-12-01', CURRENT_DATE, INTERVAL '1 month') AS g
)
SELECT e.stk_code, e.as_of, f.foreign_investment_ratio_pct
FROM e LEFT JOIN pop.foreign_ownership f ON f.stk_code = e.stk_code AND f.as_of = e.as_of
WHERE f.foreign_investment_ratio_pct IS NULL
```

### C4 validity — 外資持股比介於 0 與投資上限之間，資料日落後取樣日 ≤ 12 天
```sql
SELECT stk_code, as_of, data_date, foreign_investment_ratio_pct, foreign_investment_limit_ratio_pct
FROM pop.foreign_ownership
WHERE stk_code IN ('2330','2317','6488')
  AND (foreign_investment_ratio_pct < 0
       OR foreign_investment_ratio_pct > foreign_investment_limit_ratio_pct
       OR data_date < as_of - 12)
```

### C5 availability — 已爬的借券 raw 列都可用
```sql
SELECT stk_code, as_of
FROM pop.raw_sbl_history
WHERE stk_code IN ('2330','2317','6488') AND as_of >= DATE '2025-12-05'
  AND (sbl_history IS NULL OR sbl_history->>'found' = 'false')
```

## 已知限制
- 每月只取樣一天（5 日），法人買賣超是當日數字，不代表整月；決策只看方向與比例。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-25 | FAIL（C1、C3） | [run](../runs/2026-11-25_Q004.md) | 假日取樣缺；6488 無外資持股 |
