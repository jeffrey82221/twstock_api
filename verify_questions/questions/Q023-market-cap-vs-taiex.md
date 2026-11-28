---
id: Q023
title: 上市大盤股價淨值比在近 12 個月第幾百分位？資料庫估算的大盤市值走勢和加權指數一致嗎？
strategy: market             # §5.1 市場
status: passing
stocks: []                   # 市場層級
window: {since: 2025-11-05, until: 2026-11-27}
pop_tables: [market_valuation_summary]
crawl: []                    # 出題時（2026-11-27）2017-05 起每月都已在 pop（Q006 C1）
issues: []
last_run: 2026-11-27
---

## 決策情境
大盤 PBR 高於近 12 個月第 80 百分位 → 減碼一成；低於第 20 百分位 → 加碼一成；其間不動。
大盤數字是 adapter 用「實收資本額 ÷ 10」估股數加總出來的（[Q006](Q006-market-per-percentile.md) 已知限制：沒有獨立的錯值防線），C2 用加權指數驗它的走勢；C2 FAIL 時結論不可信。

## 決策 SQL
```sql
WITH m AS (
  SELECT data_date, market_pbr FROM pop.market_valuation_summary
  WHERE data_date >= DATE '2025-11-05' AND market_pbr IS NOT NULL
), latest AS (SELECT * FROM m ORDER BY data_date DESC LIMIT 1)
SELECT l.data_date, round(l.market_pbr::numeric, 3) AS market_pbr,
       round(100.0 * (SELECT count(*) FROM m WHERE m.market_pbr <= l.market_pbr) / (SELECT count(*) FROM m), 1) AS pbr_pctile,
       round((SELECT min(market_pbr) FROM m)::numeric, 3) AS pbr_min, round((SELECT max(market_pbr) FROM m)::numeric, 3) AS pbr_max,
       (SELECT count(*) FROM m) AS n_months
FROM latest l
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 窗口內每月 5 日的取樣都有大盤 PBR 與總市值
```sql
SELECT (date_trunc('month', g) + INTERVAL '4 days')::date AS missing_valuation_date
FROM generate_series(DATE '2025-11-01', CURRENT_DATE - 4, INTERVAL '1 month') AS g
WHERE NOT EXISTS (SELECT 1 FROM pop.market_valuation_summary m
                  WHERE m.valuation_date = (date_trunc('month', g) + INTERVAL '4 days')::date
                    AND m.market_pbr IS NOT NULL AND m.total_market_cap > 0)
```

### C2 reasonableness — 估算總市值的月變動倍數 ÷ 加權指數的月變動倍數介於 0.97 與 1.03
加權指數（TWSE FMTQIK，管線沒有用到的 endpoint）是市值加權、會用除數調整新上市與增資，所以兩者的月變動應該幾乎相同；差異來自新上市、增減資與估算股數（面額 10 元假設）。2025-12 ～ 2026-11 實測比值 0.984–1.014（2026-11-27）。單位錯、漏掉一大批成分股、收盤價取錯日都會超出 ±3%。
```sql
WITH taiex(d, idx) AS (VALUES
  -- 出處：TWSE 每日市場成交資訊 FMTQIK「發行量加權股價指數」（www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date=<月>），取得於 2026-11-27 19:55
  (DATE '2025-11-05', 27717.06),
  (DATE '2025-12-05', 27980.89),
  (DATE '2026-01-05', 30105.04),
  (DATE '2026-02-05', 31801.27),
  (DATE '2026-03-05', 33672.94),
  (DATE '2026-04-02', 32572.43),
  (DATE '2026-05-05', 40769.29),
  (DATE '2026-06-05', 45070.94),
  (DATE '2026-07-03', 46780.62),
  (DATE '2026-08-05', 44611.60),
  (DATE '2026-09-04', 46551.13),
  (DATE '2026-10-05', 49712.04),
  (DATE '2026-11-05', 41574.72)
), m AS (
  SELECT data_date, total_market_cap, lag(total_market_cap) OVER (ORDER BY data_date) AS prev_cap,
         lag(data_date) OVER (ORDER BY data_date) AS prev_date
  FROM pop.market_valuation_summary WHERE data_date >= DATE '2025-11-05'
)
SELECT m.data_date, round((m.total_market_cap / m.prev_cap)::numeric, 4) AS cap_ratio,
       round((t.idx / tp.idx)::numeric, 4) AS taiex_ratio,
       round(((m.total_market_cap / m.prev_cap) / (t.idx / tp.idx))::numeric, 4) AS rel
FROM m JOIN taiex t ON t.d = m.data_date JOIN taiex tp ON tp.d = m.prev_date
WHERE abs((m.total_market_cap / m.prev_cap) / (t.idx / tp.idx) - 1) > 0.03
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；預期 12）：
```sql
WITH taiex(d, idx) AS (VALUES
  -- 出處：TWSE 每日市場成交資訊 FMTQIK「發行量加權股價指數」（www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date=<月>），取得於 2026-11-27 19:55
  (DATE '2025-11-05', 27717.06),
  (DATE '2025-12-05', 27980.89),
  (DATE '2026-01-05', 30105.04),
  (DATE '2026-02-05', 31801.27),
  (DATE '2026-03-05', 33672.94),
  (DATE '2026-04-02', 32572.43),
  (DATE '2026-05-05', 40769.29),
  (DATE '2026-06-05', 45070.94),
  (DATE '2026-07-03', 46780.62),
  (DATE '2026-08-05', 44611.60),
  (DATE '2026-09-04', 46551.13),
  (DATE '2026-10-05', 49712.04),
  (DATE '2026-11-05', 41574.72)
), m AS (
  SELECT data_date, lag(data_date) OVER (ORDER BY data_date) AS prev_date
  FROM pop.market_valuation_summary WHERE data_date >= DATE '2025-11-05'
)
SELECT count(*) FROM m JOIN taiex t ON t.d = m.data_date JOIN taiex tp ON tp.d = m.prev_date
```

### C3 uniqueness — 同一個實際交易日只出現在一個取樣日
```sql
SELECT data_date, array_agg(valuation_date ORDER BY valuation_date) AS valuation_dates
FROM pop.market_valuation_summary WHERE data_date IS NOT NULL GROUP BY 1 HAVING count(*) > 1
```

### C4 timeliness — 實際交易日不晚於取樣日、落後 ≤ 12 天
```sql
SELECT valuation_date, data_date FROM pop.market_valuation_summary
WHERE data_date >= DATE '2025-11-01' AND (data_date > valuation_date OR data_date < valuation_date - 12)
```

## 已知限制
- C2 只驗「走勢」：估算總市值的水準（2026-11-05 約 135.7 兆）因為用實收資本額 ÷ 10 估股數，對面額不是 10 元或有特別股的公司會偏差（[Q017](Q017-basic-info-capital-listing.md) 已知限制），這題不驗水準。
- golden 只有窗口內 13 個取樣日的加權指數；延長窗口時要補 golden。
- 12 個月只有 13 個點，百分位粒度約 7.7 個百分點。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-27 | passing（4／4） | [run](../runs/2026-11-27_Q023.md) | 大盤 PBR 第 53.8 百分位 → 不調整；市值走勢與加權指數一致 |
