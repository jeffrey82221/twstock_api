---
id: I003
title: dividend_yfinance 的現金股利是配股還原後的值，與實際配發金額不一致
severity: S1
layer: null
status: open
pop_tables: [dividend_yfinance, raw_dividend_yfinance, raw_dividend_history_yfinance]
seeds: [dividend_event_yfinance_list]
found_by: [Q005]
---

## 症狀（附可重現的 SQL）

FinMind 與 yfinance 同一除息日的現金股利，41 組可比對、3 組不符，且比值固定：

| stk_code | 除息日 | FinMind | yfinance | 比值 |
| --- | --- | ---: | ---: | ---: |
| 1101 | 2021-08-12 | 3.368 | 3.062 | 1.100 |
| 2882 | 2021-07-26 | 2.500 | 2.461 | 1.016 |
| 2882 | 2022-07-04 | 3.500 | 3.446 | 1.016 |

比值固定、只出現在之後有配股的公司，推測 yfinance 依之後的配股做了還原調整。`dividend` 與 `dividend_yfinance` 欄位名稱相同（`cash_dividend`），語意卻不同（S1）。

```sql
SELECT f.stk_code, f.reference_date, f.cash_dividend AS finmind, y.cash_dividend AS yfinance,
       round(f.cash_dividend / NULLIF(y.cash_dividend, 0), 4) AS ratio
FROM pop.dividend f JOIN pop.dividend_yfinance y USING (stk_code, reference_date)
WHERE abs(f.cash_dividend - y.cash_dividend) > 0.01
```

## 證據

（指令 4 填寫）

## 根因

（指令 4 填寫）

## 修復方案

（指令 4 填寫）

## 修復紀錄

## 重爬與驗證
