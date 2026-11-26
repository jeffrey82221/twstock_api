---
id: Q005
title: 2330、2317、2882、1101 是否連續 5 年配發現金股利？目前殖利率多少？
strategy: income
status: failing
stocks: ["2330", "2317", "2882", "1101"]
window: {since: 2021, until: 2026-11-25}
pop_tables: [dividend, dividend_yfinance, company_valuation]
crawl:
  - seed: company_list                       # 1101 尚未進母體（Q003 會先補）
    filters: {stk_code: "1101"}
    budget: 1
  - seed: company_finmind_list
    filters: {stk_code: ["2330", "2317", "2882", "1101"]}
    budget: 4                # FinMind
  - seed: dividend_event_list
    filters: {stk_code: ["2330", "2317", "2882", "1101"]}
    order_by: ["reference_date"]
    budget: 16               # FinMind；每檔最近 4 次
  - seed: dividend_event_yfinance_list
    filters: {stk_code: ["2330", "2317", "2882", "1101"]}
    order_by: ["reference_date"]
    budget: 40               # yfinance；季配息的 2330 需 20 次才涵蓋 5 年
  - seed: company_valuation_monthly_list
    filters: {stk_code: ["2882", "1101"]}
    order_by: ["valuation_date"]
    budget: 12               # TWSE；最近 6 個月
issues: [I003]
last_run: 2026-11-25
---

## 決策情境
2021–2025 每年都有現金股利、且目前殖利率 > 4% → 列入存股名單。兩個股利來源（FinMind、yfinance）
必須一致才採信。

## 決策 SQL
```sql
WITH d AS (
  SELECT stk_code, extract(year FROM reference_date)::int AS y, sum(cash_dividend) AS cash
  FROM pop.dividend_yfinance
  WHERE stk_code IN ('2330','2317','2882','1101') GROUP BY 1, 2
), v AS (
  SELECT DISTINCT ON (stk_code) stk_code, as_of, dividend_yield_pct
  FROM pop.company_valuation WHERE stk_code IN ('2330','2317','2882','1101')
  ORDER BY stk_code, as_of DESC
)
SELECT s.stk_code,
       count(*) FILTER (WHERE d.y BETWEEN 2021 AND 2025 AND d.cash > 0) AS years_paid_2021_2025,
       max(v.as_of) AS yield_as_of, max(v.dividend_yield_pct) AS dividend_yield_pct
FROM unnest(ARRAY['2330','2317','2882','1101']) AS s(stk_code)
LEFT JOIN d ON d.stk_code = s.stk_code
LEFT JOIN v ON v.stk_code = s.stk_code
GROUP BY s.stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 cross-source — FinMind 與 yfinance 同一除息基準日的現金股利一致（≤ 0.01 元）
```sql
SELECT f.stk_code, f.reference_date, f.cash_dividend AS finmind_cash, y.cash_dividend AS yfinance_cash
FROM pop.dividend f
JOIN pop.dividend_yfinance y ON y.stk_code = f.stk_code AND y.reference_date = f.reference_date
WHERE f.stk_code IN ('2330','2317','2882','1101')
  AND abs(COALESCE(f.cash_dividend, 0) - COALESCE(y.cash_dividend, 0)) > 0.01
```
可比對列數（必須 > 0，否則 INCONCLUSIVE）：
```sql
SELECT count(*) FROM pop.dividend f
JOIN pop.dividend_yfinance y ON y.stk_code = f.stk_code AND y.reference_date = f.reference_date
WHERE f.stk_code IN ('2330','2317','2882','1101')
```

### C2 completeness — 2021–2025 每年至少一筆 yfinance 現金股利事件
```sql
WITH e AS (
  SELECT s.stk_code, y FROM unnest(ARRAY['2330','2317','2882','1101']) AS s(stk_code)
  CROSS JOIN generate_series(2021, 2025) AS y
)
SELECT e.stk_code, e.y AS missing_year
FROM e
WHERE NOT EXISTS (SELECT 1 FROM pop.dividend_yfinance d
                  WHERE d.stk_code = e.stk_code AND extract(year FROM d.reference_date) = e.y
                    AND d.cash_dividend > 0)
```

### C3 internal — 殖利率 = DPS ÷ 收盤價（誤差 0.2 個百分點）
```sql
SELECT stk_code, as_of, dps, close_price, dividend_yield_pct,
       round(100 * dps / NULLIF(close_price, 0), 2) AS implied_yield
FROM pop.company_valuation
WHERE stk_code IN ('2330','2317','2882','1101') AND as_of >= DATE '2025-11-01'
  AND abs(100 * dps / NULLIF(close_price, 0) - dividend_yield_pct) > 0.2
```

### C4 availability — 股利歷史（事件母體來源）都可用
```sql
SELECT 'yfinance' AS src, stk_code, dividend_history->>'reason' AS reason
FROM pop.raw_dividend_history_yfinance
WHERE stk_code IN ('2330','2317','2882','1101')
  AND (dividend_history IS NULL OR dividend_history->>'found' = 'false')
UNION ALL
SELECT 'finmind', stk_code, dividend_history->>'reason'
FROM pop.raw_dividend_history
WHERE stk_code IN ('2330','2317','2882','1101')
  AND (dividend_history IS NULL OR dividend_history->>'found' = 'false')
```

## 已知限制
- 2330 自 2019 年起季配息，2317／2882／1101 為年配息；C2 只要求每年至少一筆。
- yfinance 的 reference_date 是除息日，FinMind 的是基準日／除息日，C1 只比對兩者剛好同日的事件。
- **C1 刻意維持 FAIL**（使用者 2026-11-25 決定，I003）：2882 的 yfinance 股利比實際多 1.0157 倍且 Yahoo 沒有 split 紀錄（L0），留著當作「yfinance 股利不可當實際配發金額」的提醒。回歸時只有 2882 以外的新違規才算退化；決策一律用 FinMind（`pop.dividend`）。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
| 2026-11-25 | FAIL（C1） | [run](../runs/2026-11-25_Q005.md) | yfinance 股利為還原值 |
| 2026-11-25 | FAIL（C1） | — | 指令 5 後重跑：C1 違規 3 → 2（1101 修正；2882 為 L0 殘留，見 I003） |
| 2026-11-26 | FAIL（C1） | — | C1 依使用者決定保留 FAIL（I003）；I012 修正後可比對 32 → 50，違規 3；C2–C4 PASS |
