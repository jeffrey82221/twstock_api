---
id: I008
title: financial_analysis_year_list 產生尚未公布的當年度年報列
severity: S4
layer: null
status: open
pop_tables: [raw_financial_analysis, financial_analysis]
seeds: [financial_analysis_year_list]
found_by: [Q003]
---

## 症狀（附可重現的 SQL）

年報（MOPS t51sb02）隔年才公布，seed 卻包含當年度 2026：`raw_financial_analysis` 2026 年 19／19 列（指令 1 盤點）全部 NULL，
Q003 的三檔同樣全部 NULL。每列都是必定作廢的 API 呼叫，且 seed 列一旦 insert 就凍結，明年公布後也不會自動更新。

```sql
SELECT year, count(*) AS total, count(*) FILTER (WHERE financial_analysis IS NULL) AS null_rows
FROM pop.raw_financial_analysis GROUP BY 1 ORDER BY 1 DESC LIMIT 3
```

## 證據

（指令 4 填寫）

## 根因

（指令 4 填寫）

## 修復方案

（指令 4 填寫）

## 修復紀錄

## 重爬與驗證
