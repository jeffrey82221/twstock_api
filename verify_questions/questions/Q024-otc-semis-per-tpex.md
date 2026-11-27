---
id: Q024
title: 上櫃半導體 5483、3105、3264 目前本益比在近 12 個月第幾百分位？TPEx 的估值收盤價和日 K 對得上嗎？
strategy: valuation          # §5.1 估值（上櫃邊界）
status: passing
stocks: ["5483", "3105", "3264"]
window: {since: 2025-12-05, until: 2026-11-27}
pop_tables: [company_valuation, ohlcv_daily]
crawl:                       # 出題時（2026-11-27）三檔都在母體（上櫃），估值只有 5483 一列、日 K 都沒有
  - seed: company_valuation_monthly_list
    filters: {stk_code: ["5483", "3105", "3264"]}
    order_by: ["valuation_date"]
    since: 2025-12-05
    budget: 36               # TPEx peQryDate（I005）；3 檔 × 12 個月
  - seed: ohlcv_monthly_list
    filters: {stk_code: ["5483", "3105", "3264"]}
    order_by: ["month_start_date"]
    since: 2025-12-01
    budget: 36               # TPEx tradingStock；3 檔 × 12 個月（合計 72，§8.3 TWSE／TPEx 上限 120）
issues: []
last_run: 2026-11-27
---

## 決策情境
目前 PER 低於自身近 12 個月第 20 百分位 → 列入觀察；高於第 80 百分位 → 不追價。上櫃股目前只有 6488 驗過 TPEx 路徑（Q001、Q011），這題擴大到三檔。
虧損期間 PER 為 NULL，百分位只算有值的月份；C2 FAIL 時估值不可信。

## 決策 SQL
```sql
WITH v AS (
  SELECT stk_code, as_of, per FROM pop.company_valuation
  WHERE stk_code IN ('5483','3105','3264') AND as_of >= DATE '2025-12-05' AND per IS NOT NULL
), latest AS (SELECT DISTINCT ON (stk_code) stk_code, as_of, per FROM v ORDER BY stk_code, as_of DESC)
SELECT l.stk_code, l.as_of, l.per,
       round(100.0 * (SELECT count(*) FROM v WHERE v.stk_code = l.stk_code AND v.per <= l.per)
             / (SELECT count(*) FROM v WHERE v.stk_code = l.stk_code), 1) AS per_pctile,
       (SELECT count(*) FROM v WHERE v.stk_code = l.stk_code) AS n_months_with_per
FROM latest l ORDER BY l.stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 每檔每月 5 日都有估值列（收盤價有值），每月都有日 K
```sql
WITH e AS (
  SELECT s.stk_code, (date_trunc('month', g) + INTERVAL '4 days')::date AS as_of, date_trunc('month', g)::date AS m
  FROM unnest(ARRAY['5483','3105','3264']) AS s(stk_code)
  CROSS JOIN generate_series(DATE '2025-12-01', CURRENT_DATE - 4, INTERVAL '1 month') AS g
)
SELECT e.stk_code, e.as_of,
       EXISTS (SELECT 1 FROM pop.company_valuation v WHERE v.stk_code = e.stk_code AND v.as_of = e.as_of AND v.close_price IS NOT NULL) AS has_val,
       EXISTS (SELECT 1 FROM pop.ohlcv_daily o WHERE o.stk_code = e.stk_code AND date_trunc('month', o.trade_date) = e.m AND o.close IS NOT NULL) AS has_ohlcv
FROM e
WHERE NOT EXISTS (SELECT 1 FROM pop.company_valuation v WHERE v.stk_code = e.stk_code AND v.as_of = e.as_of AND v.close_price IS NOT NULL)
   OR NOT EXISTS (SELECT 1 FROM pop.ohlcv_daily o WHERE o.stk_code = e.stk_code AND date_trunc('month', o.trade_date) = e.m AND o.close IS NOT NULL)
```

### C2 cross-source — 估值收盤價（TPEx peQryDate）= 同日日 K 收盤價（TPEx tradingStock）（誤差 0.5%）
兩個不同的 TPEx endpoint 與 adapter（`app/valuation_source.py` vs `app/ohlcv_source.py`）。
```sql
SELECT v.stk_code, v.data_date, v.close_price, o.close
FROM pop.company_valuation v JOIN pop.ohlcv_daily o ON o.stk_code = v.stk_code AND o.trade_date = v.data_date
WHERE v.stk_code IN ('5483','3105','3264') AND v.as_of >= DATE '2025-12-05'
  AND abs(v.close_price - o.close) > 0.005 * o.close
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；補爬後預期 36）：
```sql
SELECT count(*) FROM pop.company_valuation v JOIN pop.ohlcv_daily o ON o.stk_code = v.stk_code AND o.trade_date = v.data_date
WHERE v.stk_code IN ('5483','3105','3264') AND v.as_of >= DATE '2025-12-05' AND v.close_price IS NOT NULL
```

### C3 precision — 收盤價落在升降單位上（日 K 與估值兩邊）、PER／PBR 到小數第二位
上櫃的升降單位與上市相同（README §7.1 precision 範例）。
```sql
SELECT 'ohlcv' AS src, stk_code, trade_date AS d, close AS price, NULL::numeric AS per FROM pop.ohlcv_daily
WHERE stk_code IN ('5483','3105','3264') AND trade_date >= DATE '2025-12-01' AND close IS NOT NULL
  AND close % CASE WHEN close < 10 THEN 0.01 WHEN close < 50 THEN 0.05 WHEN close < 100 THEN 0.1
                   WHEN close < 500 THEN 0.5 WHEN close < 1000 THEN 1 ELSE 5 END <> 0
UNION ALL
SELECT 'valuation', stk_code, data_date, close_price, per FROM pop.company_valuation
WHERE stk_code IN ('5483','3105','3264') AND as_of >= DATE '2025-12-05'
  AND (per * 100 <> round(per * 100) OR pbr * 100 <> round(pbr * 100)
       OR close_price % CASE WHEN close_price < 10 THEN 0.01 WHEN close_price < 50 THEN 0.05 WHEN close_price < 100 THEN 0.1
                             WHEN close_price < 500 THEN 0.5 WHEN close_price < 1000 THEN 1 ELSE 5 END <> 0)
```

### C4 reasonableness — 單日漲跌不超過 10%（用 `change` 推前一日參考價），成交均價在漲跌停內（容許 TPEx 仟股／仟元捨入，同 Q018 C3）
```sql
SELECT stk_code, trade_date, close, change, volume, trade_value
FROM pop.ohlcv_daily
WHERE stk_code IN ('5483','3105','3264') AND trade_date >= DATE '2025-12-01'
  AND change IS NOT NULL AND close - change > 0
  AND (abs(change) > 0.10 * (close - change) + 0.01
       OR (volume > 0 AND ((trade_value + 500) / greatest(volume - 500, 1) < (close - change) * 0.9 * 0.999
                           OR (trade_value - 500) / (volume + 500) > (close - change) * 1.1 * 1.001)))
```

### C5 uniqueness — 估值表同一檔同一實際交易日只出現在一個 as_of
```sql
SELECT stk_code, data_date, array_agg(as_of ORDER BY as_of) AS as_ofs
FROM pop.company_valuation WHERE stk_code IN ('5483','3105','3264') AND data_date IS NOT NULL
GROUP BY 1, 2 HAVING count(*) > 1
```

## 已知限制
- TPEx 的成交量與成交金額單位是仟股／仟元（Q018 已知限制），均價只有千元精度；大型上櫃股影響可忽略。
- 虧損期間 TPEx 不給 PER（與 TWSE 相同），PER 為 NULL 的月份不算進百分位。
- 三檔都是上櫃半導體，結論只代表這三檔，不代表上櫃半導體整體（Q022：母體覆蓋率不到一半）。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-27 | passing（5／5） | [run](../runs/2026-11-27_Q024.md) | 補爬 TPEx 72 列；三檔 PER 第 16.7 百分位 → 觀察；TPEx 官網抽查一致 |
