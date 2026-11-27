---
id: Q038
title: 上市大盤股價淨值比在近 5 年第幾百分位？5 年來估算總市值的每月變動都和加權指數一致嗎？
strategy: market             # §5.1 市場
status: passing
stocks: []                   # 市場層級
window: {since: 2021-11-05, until: 2026-11-27}
pop_tables: [market_valuation_summary]
crawl: []                    # 2017-05 起每月都已在 pop（Q006 C1）
issues: []
last_run: 2026-11-28
---

## 決策情境
大盤 PBR 高於近 5 年第 80 百分位 → 減碼一成；低於第 20 百分位 → 加碼一成。Q006 用 PER、近 5 年，Q023 用 PBR、近 12 個月；這題用 PBR、近 5 年，並把 Q023 的「市值走勢 vs 加權指數」防線延伸到 Q006 的整個窗口（Q006 已知限制：沒有錯值防線）。C2 FAIL 時結論不可信。

## 決策 SQL
```sql
WITH m AS (SELECT data_date, market_pbr FROM pop.market_valuation_summary WHERE data_date >= DATE '2021-11-05' AND market_pbr IS NOT NULL),
     latest AS (SELECT * FROM m ORDER BY data_date DESC LIMIT 1)
SELECT l.data_date, round(l.market_pbr::numeric, 3) AS market_pbr,
       round(100.0 * (SELECT count(*) FROM m WHERE m.market_pbr <= l.market_pbr) / (SELECT count(*) FROM m), 1) AS pbr_pctile_5y,
       (SELECT count(*) FROM m) AS n_months
FROM latest l
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 2021-11 起每月 5 日的取樣都有 PBR 與總市值
```sql
SELECT (date_trunc('month', g) + INTERVAL '4 days')::date AS missing_valuation_date
FROM generate_series(DATE '2021-11-01', CURRENT_DATE - 4, INTERVAL '1 month') AS g
WHERE NOT EXISTS (SELECT 1 FROM pop.market_valuation_summary m
                  WHERE m.valuation_date = (date_trunc('month', g) + INTERVAL '4 days')::date AND m.market_pbr IS NOT NULL AND m.total_market_cap > 0)
```

### C2 reasonableness — 估算總市值月變動 ÷ 加權指數月變動 介於 0.97 與 1.03（同 Q023 C2，窗口 5 年）
2021-12 ～ 2026-11 共 60 個月實測 0.9844–1.0143（2026-11-28）。
```sql
WITH taiex(d, idx) AS (VALUES
  -- 出處：TWSE 每日市場成交資訊 FMTQIK「發行量加權股價指數」（www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date=<月>，2021-10 ～ 2026-11 逐月查詢），取得於 2026-11-27～28
  (DATE '2021-11-05', 17296.90),
  (DATE '2021-12-03', 17697.14),
  (DATE '2022-01-05', 18499.96),
  (DATE '2022-01-26', 17674.40),
  (DATE '2022-03-04', 17736.52),
  (DATE '2022-04-01', 17625.59),
  (DATE '2022-05-05', 16696.12),
  (DATE '2022-06-02', 16552.57),
  (DATE '2022-07-05', 14349.20),
  (DATE '2022-08-05', 15036.04),
  (DATE '2022-09-05', 14661.10),
  (DATE '2022-10-05', 13801.43),
  (DATE '2022-11-04', 13026.71),
  (DATE '2022-12-05', 14980.74),
  (DATE '2023-01-05', 14301.05),
  (DATE '2023-02-03', 15602.66),
  (DATE '2023-03-03', 15608.42),
  (DATE '2023-03-31', 15868.06),
  (DATE '2023-05-05', 15626.07),
  (DATE '2023-06-05', 16714.43),
  (DATE '2023-07-05', 17056.43),
  (DATE '2023-08-04', 16843.68),
  (DATE '2023-09-05', 16791.61),
  (DATE '2023-10-05', 16453.52),
  (DATE '2023-11-03', 16507.65),
  (DATE '2023-12-05', 17328.01),
  (DATE '2024-01-05', 17519.14),
  (DATE '2024-02-05', 18096.07),
  (DATE '2024-03-05', 19386.92),
  (DATE '2024-04-03', 20337.60),
  (DATE '2024-05-03', 20330.32),
  (DATE '2024-06-05', 21484.88),
  (DATE '2024-07-05', 23556.59),
  (DATE '2024-08-05', 19830.88),
  (DATE '2024-09-05', 21187.71),
  (DATE '2024-10-04', 22302.71),
  (DATE '2024-11-05', 23106.79),
  (DATE '2024-12-05', 23267.94),
  (DATE '2025-01-03', 22908.30),
  (DATE '2025-02-05', 23161.58),
  (DATE '2025-03-05', 22871.90),
  (DATE '2025-04-02', 21298.22),
  (DATE '2025-05-05', 20532.99),
  (DATE '2025-06-05', 21674.43),
  (DATE '2025-07-04', 22547.50),
  (DATE '2025-08-05', 23660.59),
  (DATE '2025-09-05', 24494.58),
  (DATE '2025-10-03', 26761.06),
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
  FROM pop.market_valuation_summary WHERE data_date >= DATE '2021-11-01'
)
SELECT m.data_date, round(((m.total_market_cap / m.prev_cap) / (t.idx / tp.idx))::numeric, 4) AS rel
FROM m JOIN taiex t ON t.d = m.data_date JOIN taiex tp ON tp.d = m.prev_date
WHERE abs((m.total_market_cap / m.prev_cap) / (t.idx / tp.idx) - 1) > 0.03
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；預期 60）：
```sql
WITH taiex(d, idx) AS (VALUES
  -- 出處：TWSE 每日市場成交資訊 FMTQIK「發行量加權股價指數」（www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date=<月>，2021-10 ～ 2026-11 逐月查詢），取得於 2026-11-27～28
  (DATE '2021-11-05', 17296.90),
  (DATE '2021-12-03', 17697.14),
  (DATE '2022-01-05', 18499.96),
  (DATE '2022-01-26', 17674.40),
  (DATE '2022-03-04', 17736.52),
  (DATE '2022-04-01', 17625.59),
  (DATE '2022-05-05', 16696.12),
  (DATE '2022-06-02', 16552.57),
  (DATE '2022-07-05', 14349.20),
  (DATE '2022-08-05', 15036.04),
  (DATE '2022-09-05', 14661.10),
  (DATE '2022-10-05', 13801.43),
  (DATE '2022-11-04', 13026.71),
  (DATE '2022-12-05', 14980.74),
  (DATE '2023-01-05', 14301.05),
  (DATE '2023-02-03', 15602.66),
  (DATE '2023-03-03', 15608.42),
  (DATE '2023-03-31', 15868.06),
  (DATE '2023-05-05', 15626.07),
  (DATE '2023-06-05', 16714.43),
  (DATE '2023-07-05', 17056.43),
  (DATE '2023-08-04', 16843.68),
  (DATE '2023-09-05', 16791.61),
  (DATE '2023-10-05', 16453.52),
  (DATE '2023-11-03', 16507.65),
  (DATE '2023-12-05', 17328.01),
  (DATE '2024-01-05', 17519.14),
  (DATE '2024-02-05', 18096.07),
  (DATE '2024-03-05', 19386.92),
  (DATE '2024-04-03', 20337.60),
  (DATE '2024-05-03', 20330.32),
  (DATE '2024-06-05', 21484.88),
  (DATE '2024-07-05', 23556.59),
  (DATE '2024-08-05', 19830.88),
  (DATE '2024-09-05', 21187.71),
  (DATE '2024-10-04', 22302.71),
  (DATE '2024-11-05', 23106.79),
  (DATE '2024-12-05', 23267.94),
  (DATE '2025-01-03', 22908.30),
  (DATE '2025-02-05', 23161.58),
  (DATE '2025-03-05', 22871.90),
  (DATE '2025-04-02', 21298.22),
  (DATE '2025-05-05', 20532.99),
  (DATE '2025-06-05', 21674.43),
  (DATE '2025-07-04', 22547.50),
  (DATE '2025-08-05', 23660.59),
  (DATE '2025-09-05', 24494.58),
  (DATE '2025-10-03', 26761.06),
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
  FROM pop.market_valuation_summary WHERE data_date >= DATE '2021-11-01'
)
SELECT count(*) FROM m JOIN taiex t ON t.d = m.data_date JOIN taiex tp ON tp.d = m.prev_date
```

### C3 validity — PBR 介於 0.5 與 10、PER 介於 5 與 100（台股大盤歷史範圍的寬鬆界線）
全表 115 列（2017-05 起）PBR 1.43–4.46、PER 9.66–30.01，0 違規（2026-11-28）。
```sql
SELECT data_date, market_pbr, market_per FROM pop.market_valuation_summary
WHERE data_date >= DATE '2021-11-01' AND (market_pbr NOT BETWEEN 0.5 AND 10 OR market_per NOT BETWEEN 5 AND 100)
```

## 已知限制
- golden 是 61 個取樣日的加權指數收盤；延長窗口時要補。
- C2 只驗走勢不驗水準（Q023 已知限制）。
- 大盤 PBR 是 adapter 用估算股數加總算的，PBR 本身沒有獨立來源；C2 讓「總市值」有外部防線，淨值那一側沒有。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-28 | passing（3／3） | [run](../runs/2026-11-28_Q038.md) | 大盤 PBR 近 5 年第 90.2 百分位 → 減碼一成；PBR 本身準確性 INCONCLUSIVE |
