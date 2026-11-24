---
id: I001
title: mops_quarterly_financials 的營收與 EPS 是年初至今累計值，不是單季
severity: S1
layer: null
status: open
pop_tables: [mops_quarterly_financials, raw_mops_quarterly_financials]
seeds: [mops_quarterly_financials_quarter_list]
found_by: [Q002, Q003]
---

## 症狀（附可重現的 SQL）

`mops_quarterly_financials` 一列代表一季（`fiscal_year`、`quarter`），`revenue_millions`、`eps` 卻是年初至今累計：
Q2–Q4 的值約等於前幾季加總。拿來做「季毛利率／季 EPS 連升」或季營收比較會得到錯誤結論，且數字看起來完全正常（S1）。

```sql
SELECT stk_code, fiscal_year, quarter, revenue_millions, eps
FROM pop.mops_quarterly_financials
WHERE stk_code = '2330' AND fiscal_year IN (2025, 2026) ORDER BY fiscal_year, quarter
```
2330：2026 Q1 1,134,103／22.08 → Q2 2,404,483／49.33 → Q3 3,898,727／78.75；2025 Q4 EPS 66.26 = 全年 EPS（`financial_analysis` 2025）。

- Q002 C5：季營收 vs 該季三個月月營收合計，12 組可比對、9 組不符（通過的都是 Q1）。
- Q003 C4：年 EPS vs 四季 EPS 加總，15 組全部不符（加總約為年 EPS 的 2.3 倍）。

## 證據

（指令 4 填寫）

## 根因

（指令 4 填寫）

## 修復方案

（指令 4 填寫）

## 修復紀錄

## 重爬與驗證
