---
id: Q009
title: 2330、2317、6488 近 3 個月的借券費率是否明顯上升（放空壓力）？
strategy: flow               # §5.1 籌碼
status: draft
stocks: ["2330", "2317", "6488"]
window: {since: 2025-06-05, until: 2026-11-27}
pop_tables: [sbl_history, raw_sbl_history, ohlcv_daily]
crawl:                       # 出題時（2026-11-27）三檔 2025-06 ～ 2026-11 每月都已在 pop，預估補爬 0
  - seed: sbl_history_monthly_list
    filters: {stk_code: ["2330", "2317", "6488"]}
    order_by: ["month_start_date"]
    budget: 6                # 只補 2026-12 之後新到期的取樣月
issues: []
last_run: null
---

## 決策情境
近 3 個月還券紀錄的「張數加權借券費率」高於之前的 1.5 倍 → 放空壓力升高，暫緩加碼；
低於之前的 0.7 倍 → 放空壓力解除。

## 決策 SQL
```sql
WITH s AS (
  SELECT stk_code, completion_date, quantity_lots, fee_rate_pct
  FROM pop.sbl_history
  WHERE stk_code IN ('2330','2317','6488') AND as_of >= DATE '2025-06-05'
)
SELECT stk_code,
       round(sum(quantity_lots * fee_rate_pct) FILTER (WHERE completion_date >= CURRENT_DATE - 90)
             / NULLIF(sum(quantity_lots) FILTER (WHERE completion_date >= CURRENT_DATE - 90), 0), 3) AS fee_recent_3m,
       round(sum(quantity_lots * fee_rate_pct) FILTER (WHERE completion_date < CURRENT_DATE - 90)
             / NULLIF(sum(quantity_lots) FILTER (WHERE completion_date < CURRENT_DATE - 90), 0), 3) AS fee_prior,
       sum(quantity_lots) FILTER (WHERE completion_date >= CURRENT_DATE - 90) AS lots_recent_3m
FROM s GROUP BY 1 ORDER BY 1
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 uniqueness — 同一筆還券紀錄只出現在一個 as_of（I017）
```sql
SELECT stk_code, transaction_date, transaction_type, quantity_lots, completion_date,
       array_agg(as_of ORDER BY as_of) AS as_ofs
FROM pop.sbl_history
WHERE stk_code IN ('2330','2317','6488') AND as_of >= DATE '2025-06-05'
GROUP BY stk_code, transaction_date, transaction_type, quantity_lots, fee_rate_pct, completion_date, lending_days
HAVING count(DISTINCT as_of) > 1
```

### C2 cross-source — 還券日收盤價（SBL t13sa870）= 日 K 收盤價（STOCK_DAY／TPEx）（誤差 1%）
兩個不同的 TWSE／TPEx endpoint、不同 adapter；`completion_close_price` 是上游提供的，不是推導值。抓的是日期錯位與欄位對錯。
```sql
SELECT s.stk_code, s.completion_date, s.completion_close_price, o.close
FROM pop.sbl_history s
JOIN pop.ohlcv_daily o ON o.stk_code = s.stk_code AND o.trade_date = s.completion_date
WHERE s.stk_code IN ('2330','2317','6488') AND s.as_of >= DATE '2025-06-05'
  AND abs(s.completion_close_price - o.close) > 0.01 * o.close
```
可比對列數（必須 > 0，否則 INCONCLUSIVE）：
```sql
SELECT count(*) FROM pop.sbl_history s
JOIN pop.ohlcv_daily o ON o.stk_code = s.stk_code AND o.trade_date = s.completion_date
WHERE s.stk_code IN ('2330','2317','6488') AND s.as_of >= DATE '2025-06-05' AND s.completion_close_price IS NOT NULL
```

### C3 completeness — 2025-06 起每月 5 日都有一列 raw
```sql
WITH e AS (
  SELECT s.stk_code, g::date AS as_of
  FROM unnest(ARRAY['2330','2317','6488']) AS s(stk_code)
  CROSS JOIN generate_series(DATE '2025-06-05', CURRENT_DATE, INTERVAL '1 month') AS g
)
SELECT e.stk_code, e.as_of AS missing_as_of
FROM e LEFT JOIN pop.raw_sbl_history r ON r.stk_code = e.stk_code AND r.as_of = e.as_of
WHERE r.stk_code IS NULL
```

### C4 availability — 這些 raw 列都可用（大型股每月都有還券，404 不合理）
```sql
SELECT stk_code, as_of FROM pop.raw_sbl_history
WHERE stk_code IN ('2330','2317','6488') AND as_of >= DATE '2025-06-05'
  AND (sbl_history IS NULL OR sbl_history->>'found' = 'false')
```

### C5 validity — 費率 0–20%、張數 > 0、類型只有議借／競價
上限 20% 是範圍檢查的寬鬆值，實測全表最高 16%（議借）。
```sql
SELECT stk_code, as_of, transaction_date, transaction_type, quantity_lots, fee_rate_pct
FROM pop.sbl_history
WHERE stk_code IN ('2330','2317','6488') AND as_of >= DATE '2025-06-05'
  AND (fee_rate_pct NOT BETWEEN 0 AND 20 OR quantity_lots <= 0 OR transaction_type NOT IN ('議借', '競價'))
```

### C6 reasonableness — 借券日 ≤ 還券日 ≤ as_of，借券天數 = 還券日 − 借券日
```sql
SELECT stk_code, as_of, transaction_date, completion_date, lending_days
FROM pop.sbl_history
WHERE stk_code IN ('2330','2317','6488') AND as_of >= DATE '2025-06-05'
  AND (transaction_date > completion_date OR completion_date > as_of
       OR lending_days <> completion_date - transaction_date)
```

## 已知限制
- `sbl_history` 是「還券」紀錄（t13sa870），不是每日借券賣出餘額；費率反映的是到期還券的那批合約，對近期放空壓力有落後。
- I017（借券視窗重疊）已在 2026-11-27 修正：正規化 SQL 只保留還券日落在 `(as_of − 1 個月, as_of]` 的紀錄，C1 是它的回歸防線。
- 2317、6488 的借券資料從 2025-06 起（seed 範圍），所以窗口從 2025-06-05 起。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
