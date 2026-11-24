---
id: I006
title: 上櫃股沒有外資持股資料（MI_QFIIS 只涵蓋上市）
severity: S2
layer: null
status: open
pop_tables: [raw_foreign_ownership, foreign_ownership]
seeds: [foreign_ownership_monthly_list]
found_by: [Q004]
---

## 症狀（附可重現的 SQL）

6488（上櫃）的 `raw_foreign_ownership` 全部 found=false，`foreign_ownership` 沒有任何持股比。上游 TWSE MI_QFIIS 只涵蓋上市。
上櫃股的外資持股變化無法判斷；seed 仍對每檔上櫃股每月打 API（S2 + S4）。

```sql
SELECT b.market, count(*) AS raw_rows, count(*) FILTER (WHERE r.foreign_ownership->>'found' = 'false') AS found_false
FROM pop.raw_foreign_ownership r JOIN pop.company_basic_info_list b USING (stk_code) GROUP BY 1
```

## 證據

（指令 4 填寫）

## 根因

（指令 4 填寫）

## 修復方案

（指令 4 填寫）

## 修復紀錄

## 重爬與驗證
