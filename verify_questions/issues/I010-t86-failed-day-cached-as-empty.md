---
id: I010
title: TWSE T86 下載失敗被快取成「空的交易日」，造成永久資料空洞
severity: S2
layer: L1
status: open
pop_tables: [raw_institutional_net_buy_sell, institutional_net_buy_sell]
seeds: [institutional_monthly_list]
found_by: [I007 重爬（指令 5）]
---

## 症狀（附可重現的 SQL）

修 I007 時重爬 448 列「row 為空」的法人資料，多數仍為空，且是**交易日**（例：2330 2017-06-05 週一）。
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
4. 依 §10.2 重爬 I007 留下的 448 列（排程在 TWSE 低峰、row_cnt 小）。

## 修復紀錄

## 重爬與驗證
