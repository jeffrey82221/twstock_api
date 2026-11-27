---
id: Q031
title: 上櫃 5483、3105、3264、6488 近 4 年現金股利是否逐年提高？FinMind 的上櫃股利和櫃買中心除息結果一樣嗎？
strategy: income             # §5.1 股利（上櫃邊界）
status: passing
stocks: ["5483", "3105", "3264", "6488"]
window: {since: 2023-01-01, until: 2026-11-27}
pop_tables: [dividend]
crawl:                       # 出題時（2026-11-27）5483、3105、3264 由 Q029、6488 由 Q020 補過
  - seed: dividend_event_list
    filters: {stk_code: ["5483", "3105", "3264", "6488"]}
    order_by: ["reference_date"]
    since: 2023-01-01
    budget: 4                # FinMind；只補缺的
issues: []
last_run: 2026-11-27
---

## 決策情境
以除息日所在年度加總每股現金股利：2026 年高於 2023 年、且 2024–2026 沒有一年比前一年低 → 「股利成長」，可作為存股候選；任一年下降 → 不列入。
只用 FinMind 的 `dividend`：yfinance 的上櫃股利有錯值與缺事件（I026），本題不用。C1 FAIL 時結論不可信。

## 決策 SQL
```sql
SELECT stk_code,
       round(sum(cash_dividend) FILTER (WHERE extract(year FROM reference_date) = 2023), 4) AS y2023,
       round(sum(cash_dividend) FILTER (WHERE extract(year FROM reference_date) = 2024), 4) AS y2024,
       round(sum(cash_dividend) FILTER (WHERE extract(year FROM reference_date) = 2025), 4) AS y2025,
       round(sum(cash_dividend) FILTER (WHERE extract(year FROM reference_date) = 2026), 4) AS y2026,
       count(*) AS n_events
FROM (SELECT DISTINCT stk_code, reference_date, cash_dividend FROM pop.dividend
      WHERE stk_code IN ('5483','3105','3264','6488') AND reference_date BETWEEN DATE '2023-01-01' AND DATE '2026-12-31' AND cash_dividend > 0) d
GROUP BY stk_code ORDER BY stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 accuracy — FinMind 每股現金股利 = 櫃買中心除息結果表的息值（誤差 0.000001 元）
```sql
WITH golden(stk_code, ex_date, cash) AS (VALUES
  -- 出處：TPEx 除權除息計算結果表 exDailyQ「息值」（www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?startDate=…&endDate=…，2023-01-01 ～ 2026-11-27 分 8 段查詢），取得於 2026-11-27 20:35
  ('3105', DATE '2023-07-04', 2.500000),
  ('3105', DATE '2025-06-13', 1.000000),
  ('3105', DATE '2026-06-11', 2.000000),
  ('3264', DATE '2023-06-20', 3.500000),
  ('3264', DATE '2024-06-28', 3.300000),
  ('3264', DATE '2025-06-24', 4.000000),
  ('3264', DATE '2026-06-30', 4.200000),
  ('5483', DATE '2023-01-12', 3.200000),
  ('5483', DATE '2023-07-25', 5.800000),
  ('5483', DATE '2024-01-25', 3.500000),
  ('5483', DATE '2024-07-25', 5.300000),
  ('5483', DATE '2025-01-20', 3.000000),
  ('5483', DATE '2025-07-23', 3.500000),
  ('5483', DATE '2026-01-08', 1.000000),
  ('5483', DATE '2026-07-23', 2.500000),
  ('6488', DATE '2023-01-05', 6.500000),
  ('6488', DATE '2023-07-19', 9.500000),
  ('6488', DATE '2024-01-19', 8.000000),
  ('6488', DATE '2024-07-18', 11.000000),
  ('6488', DATE '2025-01-10', 5.000000),
  ('6488', DATE '2025-07-16', 6.000000),
  ('6488', DATE '2025-12-31', 2.000000),
  ('6488', DATE '2026-07-16', 5.700000)
)
SELECT g.stk_code, g.ex_date, g.cash AS tpex, d.cash_dividend AS finmind
FROM golden g JOIN pop.dividend d ON d.stk_code = g.stk_code AND d.cash_ex_dividend_date = g.ex_date
WHERE abs(d.cash_dividend - g.cash) > 0.000001
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；預期 23）：
```sql
WITH golden(stk_code, ex_date, cash) AS (VALUES
  -- 出處：TPEx 除權除息計算結果表 exDailyQ「息值」（www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?startDate=…&endDate=…，2023-01-01 ～ 2026-11-27 分 8 段查詢），取得於 2026-11-27 20:35
  ('3105', DATE '2023-07-04', 2.500000),
  ('3105', DATE '2025-06-13', 1.000000),
  ('3105', DATE '2026-06-11', 2.000000),
  ('3264', DATE '2023-06-20', 3.500000),
  ('3264', DATE '2024-06-28', 3.300000),
  ('3264', DATE '2025-06-24', 4.000000),
  ('3264', DATE '2026-06-30', 4.200000),
  ('5483', DATE '2023-01-12', 3.200000),
  ('5483', DATE '2023-07-25', 5.800000),
  ('5483', DATE '2024-01-25', 3.500000),
  ('5483', DATE '2024-07-25', 5.300000),
  ('5483', DATE '2025-01-20', 3.000000),
  ('5483', DATE '2025-07-23', 3.500000),
  ('5483', DATE '2026-01-08', 1.000000),
  ('5483', DATE '2026-07-23', 2.500000),
  ('6488', DATE '2023-01-05', 6.500000),
  ('6488', DATE '2023-07-19', 9.500000),
  ('6488', DATE '2024-01-19', 8.000000),
  ('6488', DATE '2024-07-18', 11.000000),
  ('6488', DATE '2025-01-10', 5.000000),
  ('6488', DATE '2025-07-16', 6.000000),
  ('6488', DATE '2025-12-31', 2.000000),
  ('6488', DATE '2026-07-16', 5.700000)
)
SELECT count(DISTINCT (g.stk_code, g.ex_date)) FROM golden g
JOIN pop.dividend d ON d.stk_code = g.stk_code AND d.cash_ex_dividend_date = g.ex_date
```

### C2 completeness — 櫃買中心的每個除息事件 FinMind 都有（日期對得上），FinMind 也沒有櫃買中心沒有的現金事件
```sql
WITH golden(stk_code, ex_date, cash) AS (VALUES
  -- 出處：TPEx 除權除息計算結果表 exDailyQ「息值」（www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?startDate=…&endDate=…，2023-01-01 ～ 2026-11-27 分 8 段查詢），取得於 2026-11-27 20:35
  ('3105', DATE '2023-07-04', 2.500000),
  ('3105', DATE '2025-06-13', 1.000000),
  ('3105', DATE '2026-06-11', 2.000000),
  ('3264', DATE '2023-06-20', 3.500000),
  ('3264', DATE '2024-06-28', 3.300000),
  ('3264', DATE '2025-06-24', 4.000000),
  ('3264', DATE '2026-06-30', 4.200000),
  ('5483', DATE '2023-01-12', 3.200000),
  ('5483', DATE '2023-07-25', 5.800000),
  ('5483', DATE '2024-01-25', 3.500000),
  ('5483', DATE '2024-07-25', 5.300000),
  ('5483', DATE '2025-01-20', 3.000000),
  ('5483', DATE '2025-07-23', 3.500000),
  ('5483', DATE '2026-01-08', 1.000000),
  ('5483', DATE '2026-07-23', 2.500000),
  ('6488', DATE '2023-01-05', 6.500000),
  ('6488', DATE '2023-07-19', 9.500000),
  ('6488', DATE '2024-01-19', 8.000000),
  ('6488', DATE '2024-07-18', 11.000000),
  ('6488', DATE '2025-01-10', 5.000000),
  ('6488', DATE '2025-07-16', 6.000000),
  ('6488', DATE '2025-12-31', 2.000000),
  ('6488', DATE '2026-07-16', 5.700000)
)
SELECT 'missing_in_finmind' AS side, g.stk_code, g.ex_date, g.cash FROM golden g
WHERE NOT EXISTS (SELECT 1 FROM pop.dividend d WHERE d.stk_code = g.stk_code AND d.cash_ex_dividend_date = g.ex_date)
UNION ALL
SELECT 'extra_in_finmind', d.stk_code, d.cash_ex_dividend_date, d.cash_dividend FROM pop.dividend d
WHERE d.stk_code IN ('5483','3105','3264','6488') AND d.reference_date BETWEEN DATE '2023-01-01' AND DATE '2026-11-27' AND d.cash_dividend > 0
  AND NOT EXISTS (SELECT 1 FROM golden g WHERE g.stk_code = d.stk_code AND g.ex_date = d.cash_ex_dividend_date)
```

### C3 uniqueness — 同一檔 30 天內只有一個除息事件
```sql
SELECT a.stk_code, a.reference_date, b.reference_date AS other_date
FROM (SELECT DISTINCT stk_code, reference_date FROM pop.dividend WHERE stk_code IN ('5483','3105','3264','6488')) a
JOIN (SELECT DISTINCT stk_code, reference_date FROM pop.dividend WHERE stk_code IN ('5483','3105','3264','6488')) b
  ON b.stk_code = a.stk_code AND b.reference_date > a.reference_date AND b.reference_date - a.reference_date < 30
```

### C4 point-in-time — 公告日不晚於除息日、發放日在除息後 90 天內（同 Q020 C3、C4，合併成一個及時性檢查）
```sql
SELECT stk_code, reference_date, announcement_date, cash_ex_dividend_date, cash_payment_date FROM pop.dividend
WHERE stk_code IN ('5483','3105','3264','6488') AND reference_date >= DATE '2023-01-01'
  AND (announcement_date > coalesce(cash_ex_dividend_date, stock_ex_dividend_date)
       OR cash_payment_date < cash_ex_dividend_date OR cash_payment_date - cash_ex_dividend_date > 90)
```

## 已知限制
- golden 是 TPEx 除權除息計算結果表（上櫃）；上市股的同類斷言是 Q014（TWSE TWT49U）。
- 5483 2024-07-25 的除息日遇凱米颱風休市，TPEx 與 FinMind 都記 7/25（公告的除息日），yfinance 記 7/26；本題只用 FinMind，不受影響。
- 3105 2024 年沒有配息（TPEx 結果表沒有事件），決策的「2024 年不比前一年低」對 3105 會是否。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-27 | passing（4／4） | [run](../runs/2026-11-27_Q031.md) | 不需補爬；FinMind 對 TPEx 23／23 一致；無股利成長股 |
