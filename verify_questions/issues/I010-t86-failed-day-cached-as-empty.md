---
id: I010
title: TWSE T86 下載失敗被快取成「空的交易日」，造成永久資料空洞
severity: S2
layer: L1
status: closed
pop_tables: [raw_institutional_net_buy_sell, institutional_net_buy_sell]
seeds: [institutional_monthly_list]
found_by: [I007 重爬（指令 5）]
---

## 症狀（附可重現的 SQL）

修 [I007](I007-institutional-weekend-no-fallback.md) 時重爬 448 列「row 為空」的法人資料，多數仍為空，且是**交易日**（例：2330 2017-06-05 週一）。
backend 磁碟快取 `/tmp/institutional_cache/twse/` 共 558 個日檔，其中 487 個是空的，**329 個空檔是週一到週五**（最早 2012-05-02），檔案時間為 2026-11-23。

```sql
SELECT r.stk_code, r.as_of, extract(isodow FROM r.as_of) AS dow
FROM pop.raw_institutional_net_buy_sell r
WHERE jsonb_typeof(r.institutional_net_buy_sell->'row') IS DISTINCT FROM 'object'
  AND extract(isodow FROM r.as_of) < 6
ORDER BY r.as_of
```

## 證據

- `app/institutional_source._fetch_twse_t86_day`：下載例外 → 回 `{}` 且不快取；但只要上游回了可解析的 JSON，就把 `_parse_twse_t86_payload` 的結果（可能是 `{}`）寫進快取。TWSE 限流或錯誤時回的 JSON（`stat` 非 OK）因此被當成「非交易日」永久快取。
- 本次重爬期間 TWSE 開始回封鎖 HTML（`var contactPhone`），T86 直接請求回 307。
- 端點對「上游失敗」與「真的沒資料」都回 200 + `row: null`，DB 的 http_cache 也把它當成功快取。

## 根因

（待確認：需在 TWSE 解封後抓一個 2017 年交易日的 T86 原始回應，確認是限流回應、還是舊日期需要不同參數。）L1。

## 修復方案

1. `_fetch_twse_t86_day`：只有 `stat` 明確表示「當日無資料」時才快取空 payload；其他非 OK 的 `stat` 視為失敗（不快取、記 source error），route 回 5xx（不被 DB 快取、不凍結）。
2. 清掉 2026-11-23 起被錯誤快取的空日檔（只刪週一到週五、且 `stat` 未知的檔案；需先確認判斷方式）。
3. 補 `tests/source/test_institutional_source.py` 的限流回應案例；`tests/upstream/` 檢查 T86 舊日期的回應格式。
4. 依 §10.2 重爬 [I007](I007-institutional-weekend-no-fallback.md) 留下的 448 列（排程在 TWSE 低峰、row_cnt 小）。

## 修復紀錄

- 根因確認（TWSE 解封後實測）：被快取成空的交易日（例 2017-06-05）上游其實有資料（T86 `stat=OK`、6,923 列）；真正的非交易日回 `stat=「很抱歉，沒有符合條件的資料!」`。所以空日檔來自限流／封鎖期間的非預期回應。
- **同一個 bug 存在於所有 per-day 快取**：BWIBBU_d（估值，751 個平日空檔，抽查 3/3 其實有資料）、MI_QFIIS（封鎖頁 200 + HTML → 解析不到表頭 → 快取空）、SBL（非 OK stat → 快取「無還券」）、MI_INDEX CSV、TPEx dailyQuotes／dailyTrade。
- `app/sources.py`：新增 `twse_payload_state()`（ok／no_data／error）、`tpex_payload_state()`（stat ok 且表格日期 = 查詢日；TPEx 對超出範圍的日期會默默回今天的表）、`record_bad_payload()`。
- 各 adapter 改成**白名單**：只有明確的「無資料」才快取空 payload；其他回應視為上游失敗，不快取、記 source error：`institutional_source`（T86／dailyTrade）、`valuation_source`（BWIBBU_d）、`foreign_ownership_source`（MI_QFIIS 無表頭 → `UpstreamFetchError`）、`twse_sbl_source`（非 OK stat → `UpstreamFetchError`）、`ohlcv_source`（MI_INDEX 空 body = 假日、有內容但解析不到 = 失敗；TPEx dailyQuotes）。
- `/api/institutional-net-buy-sell`：上游失敗回 **502**（原本 200 + `row: null` 被 DB 快取並凍結）。
- 清掉 2,947 個空快取檔（法人 TWSE 487、TPEx 51、估值 1,100、外資持股 750、借券 559）；真正的假日會在需要時重抓一次。
- 測試：`test_institutional_source.py`（+5）、`test_valuation_source.py`（+2）、`test_foreign_ownership_source.py`（+2）、`test_twse_sbl_source.py`（+1）、endpoint（+1）。

## 重爬與驗證

（2026-11-26，重建後）
- 根因不只限流：**上游版面在舊日期不同，parser 一列都解析不到就把交易日快取成「非交易日」**——T86 2017-12-18 前 16 欄／2012 年 12 欄、TPEx dailyTrade 舊日期資料在 `tables[1]`（16 欄）、BWIBBU_d 2017-04 前 5 欄且沒有收盤價、MI_QFIIS 2009 年前 11 欄（固定位置把「異動原因」讀成陸資上限，S1）、MOPS t21sc03 2013 年前 10 欄（2010-01～2012-12 全部當成無營收）。全部改成**依欄位名稱解析**，並加上「上游有資料卻解析不到 → 視為失敗、不快取」的護欄。
- 同類的「上游失敗被當成無資料」還有兩處，一併修正：`/api/ohlcv` per-stock-month（STOCK_DAY／tradingStock 失敗回 `[]` → 200 空月；改為 502）、`/product-revenue`（MOPS 失敗被快取成「從未申報」24 小時；改為記 source error、不快取）。
- 再清一次快取：法人空日檔 10 個、MI_QFIIS 舊版面誤解析檔 63 個（2004-02-11～2009-04-03）。
- 結果（重建 + `retry_failed.py` 後）：`raw_institutional_net_buy_sell` 522／533 可用（剩 11 列見 [I011](I011-raw-and-normalized-immv-diverge.md)）；2330 2017-06-05 有完整法人資料；`raw_ohlcv_monthly` 空月 454 → 0；`raw_monthly_revenue_twse` 75.1% → 100%；`raw_sbl_history` 40.8% → 77.0%（其餘 85 列是 31 天視窗內真的沒有還券，http_log 404）；`raw_product_revenue` 23 → 2 列失敗。
- [Q004](../questions/Q004-flow-foreign-institutional.md)：**5／5 PASS**（C2 的外資自營商欄位在舊版面不存在，斷言改為以 0 計；外資欄已含自營，非放寬）。
- 新增 `verify_questions/retry_failed.py`（README §10.5）：重爬被凍結的上游失敗列。
