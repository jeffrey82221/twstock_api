---
id: Q027
title: 上櫃 5483、3105、3264 近 3 個月的借券費率是否明顯上升（放空壓力）？
strategy: flow               # §5.1 籌碼（上櫃邊界）
status: draft
stocks: ["5483", "3105", "3264"]
window: {since: 2025-12-05, until: 2026-11-27}
pop_tables: [sbl_history, raw_sbl_history, ohlcv_daily]
crawl:                       # 出題時（2026-11-27）三檔都沒有借券資料；日 K 已由 Q024 補到 2025-12 起
  - seed: sbl_history_monthly_list
    filters: {stk_code: ["5483", "3105", "3264"]}
    order_by: ["month_start_date"]
    since: 2025-12-05
    budget: 36               # TWSE t13sa870（上市櫃都在這個 endpoint）；3 檔 × 12 個月
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
  WHERE stk_code IN ('5483','3105','3264') AND as_of >= DATE '2025-12-05'
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
WHERE stk_code IN ('5483','3105','3264') AND as_of >= DATE '2025-12-05'
GROUP BY stk_code, transaction_date, transaction_type, quantity_lots, fee_rate_pct, completion_date, lending_days
HAVING count(DISTINCT as_of) > 1
```

### C2 cross-source — 還券日收盤價（SBL t13sa870）= 日 K 收盤價（STOCK_DAY／TPEx）（誤差 1%）
兩個不同的 TWSE／TPEx endpoint、不同 adapter；`completion_close_price` 是上游提供的，不是推導值。抓的是日期錯位與欄位對錯。
```sql
SELECT s.stk_code, s.completion_date, s.completion_close_price, o.close
FROM pop.sbl_history s
JOIN pop.ohlcv_daily o ON o.stk_code = s.stk_code AND o.trade_date = s.completion_date
WHERE s.stk_code IN ('5483','3105','3264') AND s.as_of >= DATE '2025-12-05'
  AND abs(s.completion_close_price - o.close) > 0.01 * o.close
```
可比對列數（必須 > 0，否則 INCONCLUSIVE）：
```sql
SELECT count(*) FROM pop.sbl_history s
JOIN pop.ohlcv_daily o ON o.stk_code = s.stk_code AND o.trade_date = s.completion_date
WHERE s.stk_code IN ('5483','3105','3264') AND s.as_of >= DATE '2025-12-05' AND s.completion_close_price IS NOT NULL
```

### C3 completeness — 2025-12 起每月 5 日都有一列 raw
```sql
WITH e AS (
  SELECT s.stk_code, g::date AS as_of
  FROM unnest(ARRAY['5483','3105','3264']) AS s(stk_code)
  CROSS JOIN generate_series(DATE '2025-12-05', CURRENT_DATE, INTERVAL '1 month') AS g
)
SELECT e.stk_code, e.as_of AS missing_as_of
FROM e LEFT JOIN pop.raw_sbl_history r ON r.stk_code = e.stk_code AND r.as_of = e.as_of
WHERE r.stk_code IS NULL
```

### C4 availability — 這些 raw 列都可用（上櫃股可能有月份沒有還券，違規時見已知限制）
```sql
SELECT stk_code, as_of FROM pop.raw_sbl_history
WHERE stk_code IN ('5483','3105','3264') AND as_of >= DATE '2025-12-05'
  AND (sbl_history IS NULL OR sbl_history->>'found' = 'false')
```

### C5 validity — 費率 0–20%、張數 > 0、類型只有議借／競價
上限 20% 是範圍檢查的寬鬆值，實測全表最高 16%（議借）。
```sql
SELECT stk_code, as_of, transaction_date, transaction_type, quantity_lots, fee_rate_pct
FROM pop.sbl_history
WHERE stk_code IN ('5483','3105','3264') AND as_of >= DATE '2025-12-05'
  AND (fee_rate_pct NOT BETWEEN 0 AND 20 OR quantity_lots <= 0 OR transaction_type NOT IN ('議借', '競價'))
```

### C6 reasonableness — 借券日 ≤ 還券日 ≤ as_of，借券天數 = 還券日 − 借券日
```sql
SELECT stk_code, as_of, transaction_date, completion_date, lending_days
FROM pop.sbl_history
WHERE stk_code IN ('5483','3105','3264') AND as_of >= DATE '2025-12-05'
  AND (transaction_date > completion_date OR completion_date > as_of
       OR lending_days <> completion_date - transaction_date)
```

## 已知限制
- `sbl_history` 是「還券」紀錄（t13sa870），不是每日借券賣出餘額；費率反映到期還券那批合約，對近期放空壓力有落後（同 Q009）。
- 上櫃股的借券量比上市大型股少，可能有月份沒有任何還券紀錄（raw 回 404）；C4 要求每個取樣月都可用，若違規先確認是不是真的沒有還券（合法無資料，寫進已知限制），不要直接開 issue。
- 窗口只有 12 個月（2025-12 起），「之前」的基期比 Q009 短。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
