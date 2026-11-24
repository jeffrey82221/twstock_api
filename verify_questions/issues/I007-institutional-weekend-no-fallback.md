---
id: I007
title: 法人買賣超的取樣日遇假日沒有回溯到前一個交易日
severity: S2
layer: null
status: open
pop_tables: [raw_institutional_net_buy_sell, institutional_net_buy_sell]
seeds: [institutional_monthly_list]
found_by: [Q004]
---

## 症狀（附可重現的 SQL）

`institutional_monthly_list` 每月取樣 5 日；5 日遇週末或假日時 `/api/institutional-net-buy-sell` 回空資料，該月沒有任何法人資料。
近 12 個月 2026-04-05、07-05（週日）、09-05（週六）三個月全缺，約 2／7 的取樣月份作廢（S2；也是 S4 浪費）。
`pop_raw_quality` 顯示 `raw_institutional_net_buy_sell` 100% 可用，因為回應是 found=true 但內容為空，可用率指標看不出來。
同一套取樣設計的 `company_valuation` 已有回溯（`data_date` 為實際交易日）。

```sql
SELECT stk_code, as_of, institutional_net_buy_sell->>'trade_date' AS trade_date, extract(isodow FROM as_of) AS dow
FROM pop.raw_institutional_net_buy_sell
WHERE stk_code = '2330' AND as_of >= DATE '2025-12-05' ORDER BY as_of
```

## 證據

（指令 4 填寫）

## 根因

（指令 4 填寫）

## 修復方案

（指令 4 填寫）

## 修復紀錄

## 重爬與驗證
