---
id: I006
title: 上櫃股沒有外資持股資料（MI_QFIIS 只涵蓋上市）
severity: S2
layer: L1（需新上游）
status: diagnosed
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
- [`app/foreign_ownership_source.py`](../../app/foreign_ownership_source.py) 只有 TWSE MI_QFIIS；程式註解明載「當天沒有就代表該股不在涵蓋範圍（上櫃股…）」並停止回溯，回 found=false。

## 根因

adapter 只實作上市來源；TPEx 另有僑外投資持股統計資料，尚未串接。L1。

## 修復方案

與 I005 相同的處理方式（上游研究 → contract 測試 → 依市場分流 → 重爬上櫃列），不進本輪指令 5。

## 修復紀錄

## 重爬與驗證
