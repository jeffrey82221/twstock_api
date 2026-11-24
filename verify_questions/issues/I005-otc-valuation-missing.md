---
id: I005
title: 上櫃股沒有估值資料（company_valuation 只涵蓋上市）
severity: S2
layer: null
status: open
pop_tables: [raw_company_valuation, company_valuation]
seeds: [company_valuation_monthly_list]
found_by: [Q001]
---

## 症狀（附可重現的 SQL）

`raw_company_valuation` 的 NULL 全部是上櫃股，上櫃股的可用列數為 0（211／211 列 NULL）。`/api/company-valuation` 的上游 TWSE BWIBBU_d 只涵蓋上市。
上櫃股的估值判斷（PER 百分位、殖利率）完全無法進行；seed 仍對每檔上櫃股每月打一次 API（S2 + S4）。

```sql
SELECT b.market, count(*) AS raw_rows, count(*) FILTER (WHERE r.company_valuation IS NULL) AS null_rows
FROM pop.raw_company_valuation r JOIN pop.company_basic_info_list b USING (stk_code) GROUP BY 1
```

## 證據

（指令 4 填寫）

## 根因

（指令 4 填寫）

## 修復方案

（指令 4 填寫）

## 修復紀錄

## 重爬與驗證
