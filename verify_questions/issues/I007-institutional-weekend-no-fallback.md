---
id: I007
title: 法人買賣超的取樣日遇假日沒有回溯到前一個交易日
severity: S2
layer: L1
status: diagnosed
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
- [`app/institutional_source.py`](../../app/institutional_source.py) `get_institutional_net_buy_sell`：當日全市場 payload 為空（非交易日）時回 `found=True, row=None`，沒有回溯。
- 因為 found=True，`pop_raw_quality` 把這些列算成可用（可用率指標的盲點）。
- 對照：`valuation_source` 會回溯到最近交易日並以 `data_date` 標示。

## 根因

法人 adapter 不回溯非交易日。L1。

## 修復方案

1. `institutional_source.get_institutional_net_buy_sell`：payload 為空時往前回溯，最多 10 個日曆日（全市場 payload 依日期磁碟快取，跨股共用）；`row.trade_date` 為實際交易日，頂層 `trade_date` 維持查詢日（seed 主鍵不變）。回溯 10 天仍無資料才回 `row=None`。
2. rule 18：欄位不變 → 就地修正。
3. 測試：`tests/source/` 新增 institutional 的回溯案例（目前沒有專屬 source 測試）。
4. 重爬：`raw_institutional_net_buy_sell` 中 `row` 為 null 的列（目前 Q004 三檔 9 列；全表需先查）。

## 修復紀錄

## 重爬與驗證
