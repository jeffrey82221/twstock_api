---
id: Q014
title: 2330、2317、2882、1101 的現金股利是否逐年提高？DB 的每股現金股利和證交所除息結果一樣嗎？
strategy: income             # §5.1 股利
status: draft
stocks: ["2330", "2317", "2882", "1101"]
window: {since: 2023-01-01, until: 2026-11-27}
pop_tables: [dividend, dividend_yfinance]
crawl: []                    # 出題時（2026-11-27）27 個除息事件兩張表都已有，不需補爬
issues: [I024]
last_run: null
---

## 決策情境
以除息日所在年度加總每股現金股利：2026 年合計高於 2023 年、而且 2024–2026 沒有一年比前一年低 → 「股利成長股」，可作為存股候選；
任一年下降 → 不列入。決策用 FinMind 的 `dividend`，C1 FAIL 時結論不可信。

## 決策 SQL
```sql
SELECT stk_code,
       round(sum(cash_dividend) FILTER (WHERE extract(year FROM reference_date) = 2023), 4) AS y2023,
       round(sum(cash_dividend) FILTER (WHERE extract(year FROM reference_date) = 2024), 4) AS y2024,
       round(sum(cash_dividend) FILTER (WHERE extract(year FROM reference_date) = 2025), 4) AS y2025,
       round(sum(cash_dividend) FILTER (WHERE extract(year FROM reference_date) = 2026), 4) AS y2026,
       count(*) AS n_events
FROM (SELECT DISTINCT stk_code, reference_date, cash_dividend FROM pop.dividend
      WHERE stk_code IN ('2330','2317','2882','1101') AND reference_date BETWEEN DATE '2023-01-01' AND DATE '2026-12-31') d
GROUP BY stk_code ORDER BY stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 accuracy — FinMind 每股現金股利 = 證交所除息結果表的息值（誤差 0.000001 元；TWSE 公布到小數第六位）
golden 是調整後的實際配發率，不是董事會公告的整數（例：2317 2026 年公告 7.2 元，因流通股數變動調整成 7.171792 元）。
```sql
WITH golden(stk_code, ex_date, cash) AS (VALUES
  -- 出處：TWSE 除權除息計算結果表 TWT49U「權值+息值」（www.twse.com.tw/rwd/zh/exRight/TWT49U，純除息日），取得於 2026-11-27 18:46
  ('2330', DATE '2023-03-16', 2.749820),
  ('2330', DATE '2023-06-15', 2.749820),
  ('2330', DATE '2023-09-14', 3.000000),
  ('2330', DATE '2023-12-14', 3.000000),
  ('2330', DATE '2024-03-18', 3.499789),
  ('2330', DATE '2024-06-13', 3.499789),
  ('2330', DATE '2024-09-12', 4.000138),
  ('2330', DATE '2024-12-12', 3.999637),
  ('2330', DATE '2025-03-18', 4.500020),
  ('2330', DATE '2025-06-12', 4.500020),
  ('2330', DATE '2025-09-16', 5.000017),
  ('2330', DATE '2025-12-11', 5.000011),
  ('2330', DATE '2026-03-17', 6.000035),
  ('2330', DATE '2026-06-11', 6.000035),
  ('2330', DATE '2026-09-16', 7.000001),
  ('2317', DATE '2023-07-04', 5.300000),
  ('2317', DATE '2024-07-02', 5.400000),
  ('2317', DATE '2025-07-02', 5.799983),
  ('2317', DATE '2026-07-02', 7.171792),
  ('2882', DATE '2023-06-28', 0.900000),
  ('2882', DATE '2024-07-01', 2.000000),
  ('2882', DATE '2025-06-30', 3.500000),
  ('2882', DATE '2026-06-30', 3.500000),
  ('1101', DATE '2023-07-11', 0.499719),
  ('1101', DATE '2024-07-01', 1.000000),
  ('1101', DATE '2025-07-02', 1.000000),
  ('1101', DATE '2026-07-01', 0.800000)
)
SELECT g.stk_code, g.ex_date, g.cash AS twse, d.cash_dividend AS finmind
FROM golden g JOIN pop.dividend d ON d.stk_code = g.stk_code AND d.cash_ex_dividend_date = g.ex_date
WHERE abs(d.cash_dividend - g.cash) > 0.000001
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；預期 27）：
```sql
WITH golden(stk_code, ex_date, cash) AS (VALUES
  -- 出處：TWSE 除權除息計算結果表 TWT49U「權值+息值」（www.twse.com.tw/rwd/zh/exRight/TWT49U，純除息日），取得於 2026-11-27 18:46
  ('2330', DATE '2023-03-16', 2.749820),
  ('2330', DATE '2023-06-15', 2.749820),
  ('2330', DATE '2023-09-14', 3.000000),
  ('2330', DATE '2023-12-14', 3.000000),
  ('2330', DATE '2024-03-18', 3.499789),
  ('2330', DATE '2024-06-13', 3.499789),
  ('2330', DATE '2024-09-12', 4.000138),
  ('2330', DATE '2024-12-12', 3.999637),
  ('2330', DATE '2025-03-18', 4.500020),
  ('2330', DATE '2025-06-12', 4.500020),
  ('2330', DATE '2025-09-16', 5.000017),
  ('2330', DATE '2025-12-11', 5.000011),
  ('2330', DATE '2026-03-17', 6.000035),
  ('2330', DATE '2026-06-11', 6.000035),
  ('2330', DATE '2026-09-16', 7.000001),
  ('2317', DATE '2023-07-04', 5.300000),
  ('2317', DATE '2024-07-02', 5.400000),
  ('2317', DATE '2025-07-02', 5.799983),
  ('2317', DATE '2026-07-02', 7.171792),
  ('2882', DATE '2023-06-28', 0.900000),
  ('2882', DATE '2024-07-01', 2.000000),
  ('2882', DATE '2025-06-30', 3.500000),
  ('2882', DATE '2026-06-30', 3.500000),
  ('1101', DATE '2023-07-11', 0.499719),
  ('1101', DATE '2024-07-01', 1.000000),
  ('1101', DATE '2025-07-02', 1.000000),
  ('1101', DATE '2026-07-01', 0.800000)
)
SELECT count(DISTINCT (g.stk_code, g.ex_date)) FROM golden g
JOIN pop.dividend d ON d.stk_code = g.stk_code AND d.cash_ex_dividend_date = g.ex_date
```

### C2 accuracy — yfinance 每股現金股利 = 證交所除息結果表的息值（誤差 0.000001 元）
比對範圍與 C1 不同（另一個上游與 adapter），所以同一個標籤用兩次。yfinance 的 `reference_date` 就是除息日。
```sql
WITH golden(stk_code, ex_date, cash) AS (VALUES
  -- 出處：TWSE 除權除息計算結果表 TWT49U「權值+息值」（www.twse.com.tw/rwd/zh/exRight/TWT49U，純除息日），取得於 2026-11-27 18:46
  ('2330', DATE '2023-03-16', 2.749820),
  ('2330', DATE '2023-06-15', 2.749820),
  ('2330', DATE '2023-09-14', 3.000000),
  ('2330', DATE '2023-12-14', 3.000000),
  ('2330', DATE '2024-03-18', 3.499789),
  ('2330', DATE '2024-06-13', 3.499789),
  ('2330', DATE '2024-09-12', 4.000138),
  ('2330', DATE '2024-12-12', 3.999637),
  ('2330', DATE '2025-03-18', 4.500020),
  ('2330', DATE '2025-06-12', 4.500020),
  ('2330', DATE '2025-09-16', 5.000017),
  ('2330', DATE '2025-12-11', 5.000011),
  ('2330', DATE '2026-03-17', 6.000035),
  ('2330', DATE '2026-06-11', 6.000035),
  ('2330', DATE '2026-09-16', 7.000001),
  ('2317', DATE '2023-07-04', 5.300000),
  ('2317', DATE '2024-07-02', 5.400000),
  ('2317', DATE '2025-07-02', 5.799983),
  ('2317', DATE '2026-07-02', 7.171792),
  ('2882', DATE '2023-06-28', 0.900000),
  ('2882', DATE '2024-07-01', 2.000000),
  ('2882', DATE '2025-06-30', 3.500000),
  ('2882', DATE '2026-06-30', 3.500000),
  ('1101', DATE '2023-07-11', 0.499719),
  ('1101', DATE '2024-07-01', 1.000000),
  ('1101', DATE '2025-07-02', 1.000000),
  ('1101', DATE '2026-07-01', 0.800000)
)
SELECT g.stk_code, g.ex_date, g.cash AS twse, y.cash_dividend AS yfinance
FROM golden g JOIN pop.dividend_yfinance y ON y.stk_code = g.stk_code AND y.reference_date = g.ex_date
WHERE abs(y.cash_dividend - g.cash) > 0.000001
```
可比對列數（預期 27）：
```sql
WITH golden(stk_code, ex_date, cash) AS (VALUES
  -- 出處：TWSE 除權除息計算結果表 TWT49U「權值+息值」（www.twse.com.tw/rwd/zh/exRight/TWT49U，純除息日），取得於 2026-11-27 18:46
  ('2330', DATE '2023-03-16', 2.749820),
  ('2330', DATE '2023-06-15', 2.749820),
  ('2330', DATE '2023-09-14', 3.000000),
  ('2330', DATE '2023-12-14', 3.000000),
  ('2330', DATE '2024-03-18', 3.499789),
  ('2330', DATE '2024-06-13', 3.499789),
  ('2330', DATE '2024-09-12', 4.000138),
  ('2330', DATE '2024-12-12', 3.999637),
  ('2330', DATE '2025-03-18', 4.500020),
  ('2330', DATE '2025-06-12', 4.500020),
  ('2330', DATE '2025-09-16', 5.000017),
  ('2330', DATE '2025-12-11', 5.000011),
  ('2330', DATE '2026-03-17', 6.000035),
  ('2330', DATE '2026-06-11', 6.000035),
  ('2330', DATE '2026-09-16', 7.000001),
  ('2317', DATE '2023-07-04', 5.300000),
  ('2317', DATE '2024-07-02', 5.400000),
  ('2317', DATE '2025-07-02', 5.799983),
  ('2317', DATE '2026-07-02', 7.171792),
  ('2882', DATE '2023-06-28', 0.900000),
  ('2882', DATE '2024-07-01', 2.000000),
  ('2882', DATE '2025-06-30', 3.500000),
  ('2882', DATE '2026-06-30', 3.500000),
  ('1101', DATE '2023-07-11', 0.499719),
  ('1101', DATE '2024-07-01', 1.000000),
  ('1101', DATE '2025-07-02', 1.000000),
  ('1101', DATE '2026-07-01', 0.800000)
)
SELECT count(DISTINCT (g.stk_code, g.ex_date)) FROM golden g
JOIN pop.dividend_yfinance y ON y.stk_code = g.stk_code AND y.reference_date = g.ex_date
```

### C3 completeness — 證交所的每個除息事件兩張表都有（日期對得上）
```sql
WITH golden(stk_code, ex_date, cash) AS (VALUES
  -- 出處：TWSE 除權除息計算結果表 TWT49U「權值+息值」（www.twse.com.tw/rwd/zh/exRight/TWT49U，純除息日），取得於 2026-11-27 18:46
  ('2330', DATE '2023-03-16', 2.749820),
  ('2330', DATE '2023-06-15', 2.749820),
  ('2330', DATE '2023-09-14', 3.000000),
  ('2330', DATE '2023-12-14', 3.000000),
  ('2330', DATE '2024-03-18', 3.499789),
  ('2330', DATE '2024-06-13', 3.499789),
  ('2330', DATE '2024-09-12', 4.000138),
  ('2330', DATE '2024-12-12', 3.999637),
  ('2330', DATE '2025-03-18', 4.500020),
  ('2330', DATE '2025-06-12', 4.500020),
  ('2330', DATE '2025-09-16', 5.000017),
  ('2330', DATE '2025-12-11', 5.000011),
  ('2330', DATE '2026-03-17', 6.000035),
  ('2330', DATE '2026-06-11', 6.000035),
  ('2330', DATE '2026-09-16', 7.000001),
  ('2317', DATE '2023-07-04', 5.300000),
  ('2317', DATE '2024-07-02', 5.400000),
  ('2317', DATE '2025-07-02', 5.799983),
  ('2317', DATE '2026-07-02', 7.171792),
  ('2882', DATE '2023-06-28', 0.900000),
  ('2882', DATE '2024-07-01', 2.000000),
  ('2882', DATE '2025-06-30', 3.500000),
  ('2882', DATE '2026-06-30', 3.500000),
  ('1101', DATE '2023-07-11', 0.499719),
  ('1101', DATE '2024-07-01', 1.000000),
  ('1101', DATE '2025-07-02', 1.000000),
  ('1101', DATE '2026-07-01', 0.800000)
)
SELECT g.stk_code, g.ex_date,
       EXISTS (SELECT 1 FROM pop.dividend d WHERE d.stk_code = g.stk_code AND d.cash_ex_dividend_date = g.ex_date) AS in_finmind,
       EXISTS (SELECT 1 FROM pop.dividend_yfinance y WHERE y.stk_code = g.stk_code AND y.reference_date = g.ex_date) AS in_yfinance
FROM golden g
WHERE NOT EXISTS (SELECT 1 FROM pop.dividend d WHERE d.stk_code = g.stk_code AND d.cash_ex_dividend_date = g.ex_date)
   OR NOT EXISTS (SELECT 1 FROM pop.dividend_yfinance y WHERE y.stk_code = g.stk_code AND y.reference_date = g.ex_date)
```

### C4 uniqueness — 同一檔 30 天內只有一個除息事件（同一次配息被兩個 as_of 或兩個日期各存一次）
台股沒有公司在 30 天內除息兩次（季配息最密，間隔約 3 個月）。
```sql
SELECT 'dividend' AS src, a.stk_code, a.reference_date, b.reference_date AS other_date
FROM (SELECT DISTINCT stk_code, reference_date FROM pop.dividend WHERE stk_code IN ('2330','2317','2882','1101')) a
JOIN (SELECT DISTINCT stk_code, reference_date FROM pop.dividend WHERE stk_code IN ('2330','2317','2882','1101')) b
  ON b.stk_code = a.stk_code AND b.reference_date > a.reference_date AND b.reference_date - a.reference_date < 30
UNION ALL
SELECT 'dividend_yfinance', a.stk_code, a.reference_date, b.reference_date
FROM (SELECT DISTINCT stk_code, reference_date FROM pop.dividend_yfinance WHERE stk_code IN ('2330','2317','2882','1101')) a
JOIN (SELECT DISTINCT stk_code, reference_date FROM pop.dividend_yfinance WHERE stk_code IN ('2330','2317','2882','1101')) b
  ON b.stk_code = a.stk_code AND b.reference_date > a.reference_date AND b.reference_date - a.reference_date < 30
```

### C5 validity — 現金股利 ≥ 0、股票股利 ≥ 0、至少一項 > 0，且現金股利低於除息前收盤價的 20%
20% 是寬鬆上限：台股現金殖利率單次超過 20% 幾乎只出現在減資退還股款（那不是股利）。
```sql
SELECT d.stk_code, d.reference_date, d.cash_dividend, d.stock_dividend, o.close AS prev_close
FROM (SELECT DISTINCT stk_code, reference_date, cash_dividend, stock_dividend FROM pop.dividend WHERE stk_code IN ('2330','2317','2882','1101')) d
LEFT JOIN LATERAL (SELECT close FROM pop.ohlcv_daily o WHERE o.stk_code = d.stk_code AND o.trade_date < d.reference_date
                   AND o.close IS NOT NULL ORDER BY o.trade_date DESC LIMIT 1) o ON TRUE
WHERE d.cash_dividend < 0 OR d.stock_dividend < 0 OR (coalesce(d.cash_dividend, 0) = 0 AND coalesce(d.stock_dividend, 0) = 0)
   OR (o.close IS NOT NULL AND d.cash_dividend > 0.2 * o.close)
```

### C6 field — 有現金股利的事件都有現金除息日，而且現金除息日 = 基準日（FinMind 的事件以除息日為 key，I012）
```sql
SELECT stk_code, reference_date, cash_dividend, cash_ex_dividend_date
FROM pop.dividend
WHERE stk_code IN ('2330','2317','2882','1101') AND cash_dividend > 0
  AND (cash_ex_dividend_date IS NULL OR cash_ex_dividend_date <> reference_date)
```

## 已知限制
- golden 是 TWSE 除權除息計算結果表（上市），上櫃股要改用 TPEx 的除權息結果；本題四檔都是上市。
- 決策以「除息日所在年度」加總，不是「股利所屬年度」：2330 季配息的除息日比所屬季度晚約兩季，年度合計和公司公布的年度股利不同，但逐年比較的方向一致。
- 與 Q005 的差別：Q005 C1 比的是 FinMind vs yfinance（一致性，2882 有使用者決定保留的 L0 違規）；本題兩邊都對照管線以外的 TWSE 除息結果（準確性），2023 年以前的配股還原問題（I003）不在窗口內。

- C5 出題試跑時 1 列違規：2882 2022-12-05 是國泰金現金增資的除權，FinMind 收成金額全 0 的「股利事件」（I024）。C5 的範圍刻意不限窗口：這個失效模式就是要對全歷史抓。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
