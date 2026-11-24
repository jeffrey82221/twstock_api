---
id: I002
title: as_of 看得到尚未公布的月營收與季報（前視偏誤）
severity: S1
layer: null
status: open
pop_tables: [monthly_revenue_twse, mops_quarterly_financials]
seeds: [financial_month_twse_list, mops_quarterly_financials_quarter_list]
found_by: [Q002]
---

## 症狀（附可重現的 SQL）

回測時「as_of 當天看得到的資料」不能包含當天尚未公布的數字。目前：

- `monthly_revenue_twse`：as_of = 每月 1 日，卻已有上個月營收（法定期限次月 10 日）。Q002 C3：36／36 列違規。
- `mops_quarterly_financials`：as_of = 季末日，卻已有當季財報（期限 Q1–Q3 季末後 45 天、Q4 90 天）。Q002 C4：73／73 列違規。

```sql
SELECT stk_code, as_of, latest_month_label FROM pop.monthly_revenue_twse
WHERE stk_code = '2330' AND as_of >= DATE '2026-09-01' ORDER BY as_of
```

## 證據

（指令 4 填寫）

## 根因

（指令 4 填寫）

## 修復方案

（指令 4 填寫）

## 修復紀錄

## 重爬與驗證
