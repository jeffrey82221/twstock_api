---
id: Q020
title: 2454、6488、3045 近 4 年每次配息從公告到除息、除息到發放各隔多久？FinMind 和 yfinance 的除息日與金額一致嗎？
strategy: income             # §5.1 股利
status: draft
stocks: ["2454", "6488", "3045"]
window: {since: 2023-01-01, until: 2026-11-27}
pop_tables: [dividend, dividend_yfinance]
crawl:                       # 出題時（2026-11-27）三檔在兩張股利表都沒有列；2454 的 FinMind 股利歷史也還沒爬
  - seed: company_finmind_list
    filters: {stk_code: ["2454"]}
    budget: 1                # FinMind；2454 的股利歷史（dividend_event_list 的上游）
  - seed: dividend_event_list
    filters: {stk_code: ["2454", "6488", "3045"]}
    order_by: ["reference_date"]
    since: 2023-01-01
    budget: 19               # FinMind；6488 8、3045 4、2454 約 7（四個 FinMind 步驟合計 20，§8.3 上限）
  - seed: dividend_event_yfinance_list
    filters: {stk_code: ["2454", "6488", "3045"]}
    order_by: ["reference_date"]
    since: 2023-01-01
    budget: 19               # yfinance；2454 7、6488 8、3045 4
issues: []
last_run: null
---

## 決策情境
股利再投入要算資金卡住多久：「公告 → 除息」平均 > 120 天、或「除息 → 發放」平均 > 45 天 → 現金回流慢，不適合當短期現金流部位；
兩者都較短 → 適合。C2 FAIL 時金額不可信，C3、C4 FAIL 時天數不可信。

## 決策 SQL
```sql
SELECT stk_code, count(*) AS n_events,
       round(avg(cash_ex_dividend_date - announcement_date), 1) AS avg_announce_to_ex_days,
       round(avg(cash_payment_date - cash_ex_dividend_date), 1) AS avg_ex_to_pay_days,
       round(sum(cash_dividend) FILTER (WHERE extract(year FROM reference_date) = 2025), 4) AS cash_2025
FROM (SELECT DISTINCT stk_code, reference_date, announcement_date, cash_ex_dividend_date, cash_payment_date, cash_dividend
      FROM pop.dividend WHERE stk_code IN ('2454','6488','3045') AND reference_date >= DATE '2023-01-01' AND cash_dividend > 0) d
GROUP BY stk_code ORDER BY stk_code
```

## 斷言（每句回傳違規列，0 列 = PASS）

### C1 completeness — 兩個來源的現金除息事件一一對得上（以除息日對齊）
```sql
WITH f AS (SELECT DISTINCT stk_code, cash_ex_dividend_date AS d FROM pop.dividend
           WHERE stk_code IN ('2454','6488','3045') AND reference_date >= DATE '2023-01-01' AND cash_dividend > 0),
     y AS (SELECT DISTINCT stk_code, reference_date AS d FROM pop.dividend_yfinance
           WHERE stk_code IN ('2454','6488','3045') AND reference_date >= DATE '2023-01-01' AND cash_dividend > 0)
SELECT coalesce(f.stk_code, y.stk_code) AS stk_code, coalesce(f.d, y.d) AS ex_date,
       f.d IS NOT NULL AS in_finmind, y.d IS NOT NULL AS in_yfinance
FROM f FULL JOIN y ON y.stk_code = f.stk_code AND y.d = f.d
WHERE f.d IS NULL OR y.d IS NULL
```

### C2 cross-source — 同一除息日的每股現金股利，FinMind = yfinance（誤差 0.00001 元：yfinance 小數 6 位、FinMind 8 位）
兩個不同上游與 adapter。Q014 已確認兩邊都等於 TWSE 除息結果表的調整後配息率。
```sql
SELECT d.stk_code, d.cash_ex_dividend_date, d.cash_dividend AS finmind, y.cash_dividend AS yfinance
FROM pop.dividend d JOIN pop.dividend_yfinance y ON y.stk_code = d.stk_code AND y.reference_date = d.cash_ex_dividend_date
WHERE d.stk_code IN ('2454','6488','3045') AND d.reference_date >= DATE '2023-01-01'
  AND abs(d.cash_dividend - y.cash_dividend) > 0.00001
```
可比對列數（必須 > 0，否則 INCONCLUSIVE；補爬後預期 19）：
```sql
SELECT count(*) FROM pop.dividend d JOIN pop.dividend_yfinance y ON y.stk_code = d.stk_code AND y.reference_date = d.cash_ex_dividend_date
WHERE d.stk_code IN ('2454','6488','3045') AND d.reference_date >= DATE '2023-01-01'
```

### C3 point-in-time — 公告日不晚於除權息日（看得到除息前就要先公告）
全表 60 列 0 違規（2026-11-27）。
```sql
SELECT stk_code, reference_date, announcement_date, cash_ex_dividend_date, stock_ex_dividend_date
FROM pop.dividend
WHERE stk_code IN ('2454','6488','3045') AND reference_date >= DATE '2023-01-01'
  AND announcement_date > coalesce(cash_ex_dividend_date, stock_ex_dividend_date)
```

### C4 reasonableness — 現金發放日在除息日之後 90 天內
全表 52 列有發放日，0 違規（2026-11-27）。台股實務上發放日在除息後約 3–5 週；超過 90 天或早於除息日代表欄位錯置。
```sql
SELECT stk_code, reference_date, cash_ex_dividend_date, cash_payment_date
FROM pop.dividend
WHERE stk_code IN ('2454','6488','3045') AND reference_date >= DATE '2023-01-01' AND cash_payment_date IS NOT NULL
  AND (cash_payment_date < cash_ex_dividend_date OR cash_payment_date - cash_ex_dividend_date > 90)
```

### C5 uniqueness — 同一檔 30 天內只有一個除息事件（兩張表；同 Q014 C4）
```sql
SELECT 'dividend' AS src, a.stk_code, a.reference_date, b.reference_date AS other_date
FROM (SELECT DISTINCT stk_code, reference_date FROM pop.dividend WHERE stk_code IN ('2454','6488','3045')) a
JOIN (SELECT DISTINCT stk_code, reference_date FROM pop.dividend WHERE stk_code IN ('2454','6488','3045')) b
  ON b.stk_code = a.stk_code AND b.reference_date > a.reference_date AND b.reference_date - a.reference_date < 30
UNION ALL
SELECT 'dividend_yfinance', a.stk_code, a.reference_date, b.reference_date
FROM (SELECT DISTINCT stk_code, reference_date FROM pop.dividend_yfinance WHERE stk_code IN ('2454','6488','3045')) a
JOIN (SELECT DISTINCT stk_code, reference_date FROM pop.dividend_yfinance WHERE stk_code IN ('2454','6488','3045')) b
  ON b.stk_code = a.stk_code AND b.reference_date > a.reference_date AND b.reference_date - a.reference_date < 30
```

## 已知限制
- C1、C2 只看現金股利 > 0 的事件：FinMind 會把增資／減資除權存成金額全 0 的事件（I024），yfinance 沒有這類事件，不算缺。
- 6488 是上櫃、3045 與 2454 是上市；三檔 2023 年起配息頻率不同（季配、半年配、年配），C5 的 30 天規則對季配息也成立（間隔約 3 個月）。
- 補爬時 FinMind 剛好用滿 §8.3 的 20 列上限，先看 `crawl_status(lookback_minutes=60)`。

## 歷次結果
| 日期 | 結果 | run | 備註 |
| --- | --- | --- | --- |
