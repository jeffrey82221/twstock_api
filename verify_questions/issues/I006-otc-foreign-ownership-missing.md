---
id: I006
title: 上櫃股沒有外資持股資料（MI_QFIIS 只涵蓋上市）
severity: S2
layer: L1（需新上游）
status: closed
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

（2026-11-25，同 I005）
- 上游研究：TPEx 櫃檯買賣股票僑外資及陸資持股比例 `https://www.tpex.org.tw/www/zh-tw/insti/qfii?date=YYYY/MM/DD&response=json`，欄位 `[排行, 代號, 名稱, 發行股數, 尚可投資股數, 持有股數, 尚可投資比率, 持股比率, 上限比率, 備註]`；實測最早有資料日 **2007-05-02**。
- `app/foreign_ownership_source.py`：`parse_tpex_qfii_payload()`、`_fetch_tpex_day()`（快取 `/tmp/foreign_ownership_cache/tpex/`）、`market_of()`／`source_name()`；`get_foreign_ownership(..., market=)` 依市場分流。route 回應 `source` = `TPEx qfii`。TPEx 沒有陸資上限與異動日期，`china_investment_limit_ratio_pct`／`last_change_date` 為 NULL，`change_reason` 取「備註」。
- `db/poc/foreign_ownership_monthly_list.sql`：上櫃股起點下限 2007-05-05（rule 13 對齊來源覆蓋）。
- 測試：`test_foreign_ownership_source.py`（+3）、endpoint（+1）。

## 重爬與驗證

（2026-11-26，重建後）
- `raw_foreign_ownership`：88 列全部可用，其中 15 列來自 `TPEx qfii`（之前上櫃全部 found=false）。
- Q004 C3／C4 PASS（6488 每個取樣月都有外資持股比，且在 0 與上限之間）。
