---
id: Q028
title: 上櫃 5483、3105、3264 近 12 個月取樣日的法人買賣超占成交量多少？TPEx 的法人買賣超、成交量、成交金額彼此合理嗎？
strategy: flow               # §5.1 籌碼（上櫃邊界）
status: passing
stocks: ["5483", "3105", "3264"]
window: {since: 2025-12-05, until: 2026-11-27}
pop_tables: [institutional_net_buy_sell, ohlcv_daily]
crawl: []                    # 出題時（2026-11-27）法人取樣由 Q026、日 K 由 Q024 補齊
issues: []
last_run: 2026-11-27
---

## 決策情境
取樣日三大法人合計買賣超占當日成交量的比重（絕對值）12 次中有 ≥ 6 次超過 15% → 「法人主導」，股價對法人動向敏感，
跟著 Q015 類的籌碼訊號操作；12 次都低於 5% → 「散戶主導」，籌碼訊號參考價值低。C2 或 C3 FAIL 時比重不可信。

## 決策 SQL
```sql
SELECT i.stk_code,
       string_agg(to_char(i.trade_date, 'MM-DD') || ' ' || round(100.0 * i.total_institutional_net_buy_sell / o.volume, 1), '、'
                  ORDER BY i.trade_date) AS share_pct_by_day,
       count(*) FILTER (WHERE abs(i.total_institutional_net_buy_sell) > 0.15 * o.volume) AS n_over_15pct,
       count(*) FILTER (WHERE abs(i.total_institutional_net_buy_sell) < 0.05 * o.volume) AS n_under_5pct,
       count(*) AS n_days
FROM pop.institutional_net_buy_sell i
JOIN pop.ohlcv_daily o ON o.stk_code = i.stk_code AND o.trade_date = i.trade_date
WHERE i.stk_code IN ('5483','3105','3264') AND i.as_of >= DATE '2025-12-05' AND o.volume > 0
GROUP BY i.stk_code ORDER BY i.stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 每個法人取樣日都有實際交易日，而且那天有日 K 成交量
```sql
WITH e AS (
  SELECT s.stk_code, g::date AS as_of
  FROM unnest(ARRAY['5483','3105','3264']) AS s(stk_code)
  CROSS JOIN generate_series(DATE '2025-12-05', CURRENT_DATE, INTERVAL '1 month') AS g
)
SELECT e.stk_code, e.as_of, i.trade_date, o.volume
FROM e
LEFT JOIN pop.institutional_net_buy_sell i ON i.stk_code = e.stk_code AND i.as_of = e.as_of
LEFT JOIN pop.ohlcv_daily o ON o.stk_code = e.stk_code AND o.trade_date = i.trade_date
WHERE i.trade_date IS NULL OR o.volume IS NULL
```

### C2 reasonableness — 三大法人合計、外資各自的買賣超（絕對值）不超過當日成交量（TPEx 三大法人 vs TPEx tradingStock 兩個上游）
一天的淨買超不可能大於總成交股數；超過代表單位錯（張 vs 股）、日期錯位或張冠李戴。全表 493 列可比對、0 違規（2026-11-27）。
```sql
SELECT i.stk_code, i.trade_date, i.foreign_investors_net_buy_sell, i.total_institutional_net_buy_sell, o.volume
FROM pop.institutional_net_buy_sell i
JOIN pop.ohlcv_daily o ON o.stk_code = i.stk_code AND o.trade_date = i.trade_date
WHERE i.stk_code IN ('5483','3105','3264') AND i.as_of >= DATE '2025-12-05'
  AND (abs(i.total_institutional_net_buy_sell) > o.volume OR abs(i.foreign_investors_net_buy_sell) > o.volume)
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；預期 36）：
```sql
SELECT count(*) FROM pop.institutional_net_buy_sell i
JOIN pop.ohlcv_daily o ON o.stk_code = i.stk_code AND o.trade_date = i.trade_date
WHERE i.stk_code IN ('5483','3105','3264') AND i.as_of >= DATE '2025-12-05'
```

### C3 internal — 成交均價（成交金額 ÷ 成交股數）落在當日漲跌停範圍內（容許上游千股／千元的捨入）
成交金額含鉅額與盤後交易，均價可能落在當日最高最低價之外（全表 104／24,151 列），但仍受漲跌停限制。TPEx 的成交量與金額單位是仟股／仟元，冷門股成交 1 張時均價只有千元精度（3288 2023-11-15：1,000 股、24,000 元、收盤 21），所以兩邊各容許 ±500。全表 24,030 列 0 違規（2026-11-27）。
```sql
SELECT stk_code, trade_date, close, change, volume, trade_value, round(trade_value / volume, 3) AS vwap
FROM (SELECT *, CASE WHEN trade_date >= DATE '2015-06-01' THEN 0.10 ELSE 0.07 END AS lim FROM pop.ohlcv_daily) o
WHERE stk_code IN ('5483','3105','3264') AND trade_date >= DATE '2025-12-01'
  AND volume > 0 AND close IS NOT NULL AND change IS NOT NULL AND close - change > 0
  AND ((trade_value + 500) / greatest(volume - 500, 1) < (close - change) * (1 - lim) * 0.999
       OR (trade_value - 500) / (volume + 500) > (close - change) * (1 + lim) * 1.001)
```
可比對列數（必須 > 0）：
```sql
SELECT count(*) FROM pop.ohlcv_daily
WHERE stk_code IN ('5483','3105','3264') AND trade_date >= DATE '2025-12-01'
  AND volume > 0 AND close IS NOT NULL AND change IS NOT NULL
```

### C4 validity — 成交量、成交金額、成交筆數非負；有成交量就有收盤價；法人表的市場別只有上市／上櫃
```sql
SELECT 'ohlcv' AS src, stk_code, trade_date, volume::text AS v FROM pop.ohlcv_daily
WHERE stk_code IN ('5483','3105','3264') AND trade_date >= DATE '2025-12-01'
  AND (volume < 0 OR trade_value < 0 OR transaction_count < 0 OR (volume > 0 AND close IS NULL))
UNION ALL
SELECT 'inst', stk_code, trade_date, market FROM pop.institutional_net_buy_sell
WHERE stk_code IN ('5483','3105','3264') AND as_of >= DATE '2025-12-05' AND market NOT IN ('上市', '上櫃')
```

## 已知限制
- 與 Q018 的差別：上櫃（TPEx 三大法人 vs TPEx tradingStock）。TPEx 的成交量是仟股、法人買賣超是股：C2 比的是「法人淨買超 ≤ 成交量」，成交量最多被捨入 500 股，對上限檢查沒有影響。
- 法人買賣超每月只有取樣日一天；12 次取樣的比重是粗略訊號。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-27 | passing（4／4） | [run](../runs/2026-11-27_Q028.md) | 不需補爬；5483 法人主導；TPEx 官網抽查一致 |
