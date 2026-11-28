---
id: Q011
title: 2330、2317、6488 是否站上 60 日均線？近 20 日年化波動度多少？
strategy: technical
status: passing
stocks: ["2330", "2317", "6488"]
window: {since: 2026-05-31, until: 2026-11-27}   # 最近 180 天
pop_tables: [ohlcv_daily, sbl_history]
crawl:                       # 出題時三檔近 180 天各 121 個交易日；當月那列要重爬才會更新（[I021](../issues/I021-ohlcv-current-month-frozen.md)）
  - seed: ohlcv_monthly_list
    filters: {stk_code: ["2330", "2317", "6488"]}
    order_by: ["month_start_date"]
    since: 2026-05-01        # 只補窗口內的月份
    budget: 6
issues: [I021]
last_run: 2026-11-27
---

## 決策情境
收盤價在 60 日均線之上、且 20 日年化波動度 < 40% → 趨勢完好，可續抱或分批加碼；
跌破 60 日均線 → 不加碼。

## 決策 SQL
```sql
WITH o AS (
  SELECT stk_code, trade_date, close,
         avg(close) OVER w60 AS ma60, count(*) OVER w60 AS n60,
         ln(close / lag(close) OVER (PARTITION BY stk_code ORDER BY trade_date)) AS r
  FROM pop.ohlcv_daily
  WHERE stk_code IN ('2330','2317','6488') AND trade_date >= CURRENT_DATE - 180 AND close IS NOT NULL
  WINDOW w60 AS (PARTITION BY stk_code ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
), v AS (
  SELECT stk_code, trade_date, close, ma60, n60,
         stddev_samp(r) OVER (PARTITION BY stk_code ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sd20
  FROM o
)
SELECT DISTINCT ON (stk_code) stk_code, trade_date, close, round(ma60, 2) AS ma60, n60,
       close > ma60 AS above_ma60, round((sd20 * sqrt(252) * 100)::numeric, 1) AS vol20_annual_pct
FROM v ORDER BY stk_code, trade_date DESC
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 三檔在近 180 天的聯集交易日都有收盤價
```sql
WITH cal AS (
  SELECT DISTINCT trade_date FROM pop.ohlcv_daily
  WHERE stk_code IN ('2330','2317','6488') AND trade_date >= CURRENT_DATE - 180
), e AS (
  SELECT s.stk_code, c.trade_date FROM unnest(ARRAY['2330','2317','6488']) AS s(stk_code) CROSS JOIN cal c
)
SELECT e.stk_code, e.trade_date AS missing_trade_date
FROM e LEFT JOIN pop.ohlcv_daily o ON o.stk_code = e.stk_code AND o.trade_date = e.trade_date
WHERE o.close IS NULL
```

### C2 uniqueness — 每檔每個交易日只有一列
```sql
SELECT stk_code, trade_date, count(*) AS n FROM pop.ohlcv_daily
WHERE stk_code IN ('2330','2317','6488') AND trade_date >= CURRENT_DATE - 180
GROUP BY 1, 2 HAVING count(*) > 1
```

### C3 precision — 收盤價落在升降單位上（README §7.1）
```sql
SELECT stk_code, trade_date, close FROM pop.ohlcv_daily
WHERE stk_code IN ('2330','2317','6488') AND trade_date >= CURRENT_DATE - 180 AND close IS NOT NULL
  AND close % CASE WHEN close < 10 THEN 0.01 WHEN close < 50 THEN 0.05 WHEN close < 100 THEN 0.1
                   WHEN close < 500 THEN 0.5 WHEN close < 1000 THEN 1 ELSE 5 END <> 0
```

### C4 reasonableness — OHLC 關係成立，單日漲跌不超過 10%（除權息日 `change` 為 NULL，自動排除）
```sql
SELECT stk_code, trade_date, open, high, low, close, change FROM pop.ohlcv_daily
WHERE stk_code IN ('2330','2317','6488') AND trade_date >= CURRENT_DATE - 180 AND close IS NOT NULL
  AND (high < GREATEST(open, close) OR low > LEAST(open, close)
       OR (change IS NOT NULL AND close - change > 0 AND abs(change) > 0.10 * (close - change) + 0.01))
```

### C5 cross-source — 日 K 收盤價 = 借券還券日收盤價（SBL t13sa870）（誤差 1%）
```sql
SELECT o.stk_code, o.trade_date, o.close, s.completion_close_price
FROM pop.ohlcv_daily o
JOIN (SELECT DISTINCT stk_code, completion_date, completion_close_price FROM pop.sbl_history
      WHERE completion_close_price IS NOT NULL) s
  ON s.stk_code = o.stk_code AND s.completion_date = o.trade_date
WHERE o.stk_code IN ('2330','2317','6488') AND o.trade_date >= CURRENT_DATE - 180
  AND abs(o.close - s.completion_close_price) > 0.01 * o.close
```
可比對列數（必須 > 0，否則 INCONCLUSIVE）：
```sql
SELECT count(*) FROM pop.ohlcv_daily o
JOIN (SELECT DISTINCT stk_code, completion_date FROM pop.sbl_history WHERE completion_close_price IS NOT NULL) s
  ON s.stk_code = o.stk_code AND s.completion_date = o.trade_date
WHERE o.stk_code IN ('2330','2317','6488') AND o.trade_date >= CURRENT_DATE - 180
```

### C6 freshness — 最新一筆是台北時間今天以前最近的平日（[I021](../issues/I021-ohlcv-current-month-frozen.md)）
決策用的是最新收盤價與均線，晚一兩天就可能看錯是否跌破。DB 時區是 UTC，日期一律用台北時間（README §7.2）。
```sql
WITH t AS (SELECT (now() AT TIME ZONE 'Asia/Taipei')::date AS today),
exp AS (SELECT max(d)::date AS expected FROM t, generate_series(t.today - 7, t.today - 1, INTERVAL '1 day') AS d
        WHERE extract(isodow FROM d) < 6)
SELECT o.stk_code, max(o.trade_date) AS latest_trade_date, exp.expected
FROM pop.ohlcv_daily o CROSS JOIN exp
WHERE o.stk_code IN ('2330','2317','6488')
GROUP BY o.stk_code, exp.expected
HAVING max(o.trade_date) < exp.expected
```

## 已知限制
- C6 用「最近的平日」近似交易日：國定假日（例：春節、颱風假）前後會誤判，屬 L7，記在 run 的說明欄，不開 issue。
- 對數報酬在除息日會多一個跳空（例：2330 每季配息約 1%），20 日波動度略為高估。
- 當月的日 K 在 seed 列 insert 那天就凍結（[I021](../issues/I021-ohlcv-current-month-frozen.md)），要看最新幾天必須先刪掉並重爬當月那一列（§10.2）。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-27 | passing（6／6） | [run](../runs/2026-11-27_Q011.md) | 先刪當月再重爬（[I021](../issues/I021-ohlcv-current-month-frozen.md)）；三檔都跌破 MA60 → 不加碼；官網抽查 2 筆一致 |
