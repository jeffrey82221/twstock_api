---
id: I007
title: 法人買賣超的取樣日遇假日沒有回溯到前一個交易日
severity: S2
layer: L1
status: closed
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

## 修復紀錄（2026-11-25，指令 5）

- `app/institutional_source.py`：新增 `_latest_trading_day()`，非交易日往前回溯最多 10 個日曆天；空 payload 且磁碟快取沒有該日（＝下載失敗）時不回溯。回應新增 `data_date`，`row.trade_date` 為實際交易日，頂層 `trade_date` 維持查詢日。`app/main.py` 說明同步。
- 測試：`tests/source/test_institutional_source.py`（新檔，5 個：交易日、週末回溯、長假、上限、失敗不回溯）。

## 重爬與驗證

- 直接呼叫：2330 2026-09-05（週六）→ `data_date` 2026-09-04、合計 1,969,801。
- 重爬（**未完成**）：`row` 為空的 raw 列共 448 列（112 個日期），crawl_delete_rows 全部刪除（pin、0 次 API）。重新 insert 到 288 列時發現：
  1. 大部分空列其實是**交易日**（例：2017-06-05 週一），不是假日，根因是 I010（T86 把失敗回應快取成空的交易日）；
  2. 回溯為這些日期增加 TWSE 請求，TWSE 開始回封鎖頁（`contactPhone` HTML、T86 直接請求 307），已停止重爬。
- 現況：288 列已重新 insert（多數仍空，等 I010）、160 列在 hidden 中待爬（原本就是空資料，沒有遺失有效資料）。Q004 C1 違規 9 → 8。
- 下一步：等 TWSE 解封 → 先修 I010 → 清掉錯誤的空快取 → 再依 §10.2 重爬這 448 列。

（2026-11-26，重建後）
- 指令 5 中斷時尚未重爬的列（不在備份中）以 `crawl_insert_filtered` 補 8 列（2330／2317／6488 的 2026-04-05、07-05、09-05，全為週末取樣日）→ 全部回溯到前一個交易日。
- Q004 C1 PASS。
